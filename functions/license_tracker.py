import json
import boto3
import os
from datetime import datetime
import urllib.request
import urllib.parse

def lambda_handler(event, context):
    print("License tracker started")
    try:
        result = check_expirations()
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'License expiration check completed',
                'result': result
            })
        }
    except Exception as e:
        print(f"License tracker error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'License expiration check failed',
                'details': str(e)
            })
        }

def check_expirations():
    print(f"[{datetime.now()}] Running expiration check...")
    today = datetime.today().date()
    
    # Initialize DynamoDB
    dynamodb = boto3.resource('dynamodb')
    licenses_table = dynamodb.Table('licenses')
    
    try:
        response = licenses_table.scan()
        licenses = response.get('Items', [])
        print(f"Found {len(licenses)} licenses to check")
        
        processed_count = 0
        notified_count = 0
        
        for license in licenses:
            try:
                name = license.get('name')
                expiry_str = license.get('expiry_date')
                email = license.get('email')
                owner = license.get('owner_name', 'Unknown')

                if not expiry_str:
                    continue
                    
                expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                days_left = (expiry - today).days
                print(f"Evaluating: {name} — {expiry} — {days_left} days left")
                
                if days_left in [45, 30, 15, 7, 1]:
                    # Send SNS notification
                    sns_sent = send_sns_notification(name, expiry, days_left, owner, email)
                    
                    # Send Teams alert
                    teams_sent = send_teams_message(name, expiry, days_left, owner)
                    
                    if sns_sent or teams_sent:
                        notified_count += 1
                    
                    print(f"Notified for {name}: {days_left} days left")

                processed_count += 1
                
            except Exception as e:
                print(f"Error processing license {license.get('name')}: {e}")
                continue
                
        return f"Processed {processed_count} licenses, sent {notified_count} notifications"
        
    except Exception as e:
        print(f"DynamoDB scan error: {e}")
        raise e

def send_sns_notification(name, expiry, days_left, owner, email):
    sns = boto3.client('sns')
    topic_arn = os.getenv("SNS_TOPIC_ARN")
    
    if not topic_arn:
        print("SNS_TOPIC_ARN not configured")
        return False
        
    message = f"""
🔔 **License Alert**

📄 **{name}** expires in **{days_left} days**

📅 **Expiry Date:** {expiry}
👤 **Owner:** {owner}
📧 **Contact:** {email}

Please renew this license as soon as possible to avoid disruption.
"""
    
    try:
        response = sns.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject=f"🚨 License '{name}' expires in {days_left} days"
        )
        print(f"SNS notification sent: {response['MessageId']}")
        return True
    except Exception as e:
        print(f"SNS error: {e}")
        return False

def send_teams_message(name, expiry, days_left, owner):
    webhook_url = os.getenv("TEAMS_WEBHOOK")
    if not webhook_url:
        print("TEAMS_WEBHOOK not configured")
        return False
        
    message = (
        f"🔔 **License Alert**\n"
        f"📄 `{name}` expires in **{days_left} days**\n"
        f"📅 Expiry Date: `{expiry}`\n"
        f"👤 Owner: @`{owner}`\n"
        f"📬 Please renew ASAP."
    )
    
    try:
        data = json.dumps({"text": message}).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        
        req = urllib.request.Request(webhook_url, data=data, headers=headers)
        response = urllib.request.urlopen(req)
        
        print(f"Teams message sent: {response.getcode()}")
        return response.getcode() == 200
    except Exception as e:
        print(f"Teams error: {e}")
        return False