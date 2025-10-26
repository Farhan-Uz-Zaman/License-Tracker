import json
import boto3
import re
import uuid

dynamodb = boto3.resource('dynamodb')
users_table = dynamodb.Table('users')

def is_valid_username(username):
    return re.match(r'^[a-zA-Z0-9_]{3,20}$', username) is not None

def is_valid_password(password):
    return len(password) >= 6

def lambda_handler(event, context):
    # Handle CORS preflight
    if event.get('httpMethod') == 'OPTIONS':
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-User-ID, X-Username, X-Role, x-user-id, x-username, x-role, Authorization"
            },
            "body": ""
        }
    
    body = json.loads(event['body'])
    action = body.get('action')
    username = body.get('username', '').strip()
    password = body.get('password', '').strip()

    if not is_valid_username(username):
        return json_response({"error": "Invalid username"}, 400)
    if not is_valid_password(password):
        return json_response({"error": "Invalid password"}, 400)

    if action == 'signup':
        # Check if username exists
        response = users_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('username').eq(username)
        )
        if response.get('Count', 0) > 0:
            return json_response({"error": "Username already exists"}, 409)

        # First user becomes admin
        scan = users_table.scan(Select='COUNT')
        role = 'admin' if scan['Count'] == 0 else 'general'

        user_id = str(uuid.uuid4())

        users_table.put_item(Item={
            'user_id': user_id,
            'username': username,
            'password': password,
            'role': role
        })

        return json_response({
            "message": "Signup successful",
            "user_id": user_id,
            "username": username,
            "role": role
        })

    elif action == 'login':
        # Find user by username
        response = users_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('username').eq(username)
        )
        items = response.get('Items', [])
        user = items[0] if items else None

        if user and user['password'] == password:
            return json_response({
                "message": "Login successful",
                "user_id": user['user_id'],
                "username": user['username'],
                "role": user['role']
            })
        else:
            return json_response({"error": "Invalid credentials"}, 401)

    return json_response({"error": "Invalid action"}, 400)

def json_response(data, status_code=200):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-User-ID, X-Username, X-Role, x-user-id, x-username, x-role, Authorization"
        },
        "body": json.dumps(data)
    }