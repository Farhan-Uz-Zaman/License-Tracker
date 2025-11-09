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
                primary_email = license.get('primary_email')
                primary_owner = license.get('primary_owner', 'Unknown')
                secondary_email = license.get('secondary_email')
                secondary_owner = license.get('secondary_owner', 'Unknown')

                if not expiry_str or not primary_email:
                    continue
                    
                expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                days_left = (expiry - today).days
                print(f"Evaluating: {name} — {expiry} — {days_left} days left")
                
                if days_left in [60, 45, 30] or days_left < 28:
                    # Notify both owners
                    primary_sent = send_sns_notification(name, expiry, days_left, primary_owner, primary_email)
                    secondary_sent = False
                    if secondary_email:
                        secondary_sent = send_sns_notification(name, expiry, days_left, secondary_owner, secondary_email)
                    
                    teams_sent = send_teams_message(name, expiry, days_left, primary_owner)

                    if primary_sent or secondary_sent or teams_sent:
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
    
    topic_name = f"user_topic_{email.replace('@', '_').replace('.', '_')}"
    
    try:
        topic_arn = sns.create_topic(Name=topic_name)['TopicArn']
    except Exception as e:
        print(f"Error creating topic: {e}")
        return False

    try:
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol='email',
            Endpoint=email
        )
        print(f"Subscribed {email} to topic {topic_name}")
    except Exception as e:
        if "already subscribed" in str(e).lower():
            print(f"{email} already subscribed")
        else:
            print(f"Subscription error: {e}")

    message = f"""
🔔 License Alert

📄 {name} expires in {days_left} days
📅 Expiry Date: {expiry}
👤 Owner: {owner}
📧 Contact: {email}

Please renew this license as soon as possible to avoid disruption.
"""
    try:
        response = sns.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject=f"🚨 License '{name}' expires in {days_left} days"
        )
        print(f"Notification sent to {email}: {response['MessageId']}")
        return True
    except Exception as e:
        print(f"Publish error: {e}")
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