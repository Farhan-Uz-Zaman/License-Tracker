import json
import uuid
import re
from datetime import datetime
import boto3

# Initialize DynamoDB table
dynamodb = boto3.resource('dynamodb')
licenses_table = dynamodb.Table('licenses')

# Utility: validate email format
def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

# Utility: validate date format YYYY-MM-DD
def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

# Utility: standard JSON response with CORS headers
def json_response(data, status_code=200):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-User-ID, X-Username, X-Role, x-user-id, x-username, x-role, Authorization'
        },
        'body': json.dumps(data, default=str)
    }

def lambda_handler(event, context):
    # Handle preflight OPTIONS request
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-User-ID, X-Username, X-Role, x-user-id, x-username, x-role, Authorization'
            },
            'body': json.dumps({'message': 'CORS preflight'})
        }

    method = event['httpMethod']
    path = event.get('path', '')

    if path == '/licenses' and method == 'POST':
        return add_license(event)
    elif path.startswith('/licenses/') and method == 'PUT':
        return update_license(event)

    return json_response({'error': 'Not found'}, 404)

def get_current_user(event):
    headers = event.get('headers', {})
    user_id = headers.get('x-user-id') or headers.get('X-User-ID')
    username = headers.get('x-username') or headers.get('X-Username')
    role = headers.get('x-role') or headers.get('X-Role')
    return user_id, username, role

def add_license(event):
    try:
        current_user_id, current_username, current_role = get_current_user(event)

        body = json.loads(event['body'])
        name = body['license_name'].strip()
        expiry = body['expiry_date'].strip()
        email = body['owner_email'].strip()
        owner_name = body['owner_name'].strip()

        if not is_valid_email(email):
            return json_response({'error': 'Invalid email format'}, 400)
        if not is_valid_date(expiry):
            return json_response({'error': 'Invalid date format'}, 400)

        license_id = str(uuid.uuid4())

        licenses_table.put_item(Item={
            'license_id': license_id,
            'name': name,
            'expiry_date': expiry,
            'email': email,
            'owner_name': owner_name,
            'created_by': current_user_id,
            'created_by_username': current_username,
            'created_at': datetime.now().isoformat()
        })

        return json_response({
            'message': 'License added successfully',
            'license_id': license_id
        })

    except Exception as e:
        return json_response({'error': str(e)}, 500)

def update_license(event):
    try:
        current_user_id, current_username, current_role = get_current_user(event)

        path_params = event.get('pathParameters') or {}
        license_id = path_params.get('id')  # Accepts /licenses/{id}
        if not license_id:
            return json_response({'error': 'Missing id in path'}, 400)

        body = json.loads(event['body'])
        new_expiry = body['new_expiry'].strip()

        if not is_valid_date(new_expiry):
            return json_response({'error': 'Invalid date format'}, 400)

        response = licenses_table.get_item(Key={'license_id': license_id})
        if 'Item' not in response:
            return json_response({'error': 'License not found'}, 404)

        licenses_table.update_item(
            Key={'license_id': license_id},
            UpdateExpression='SET expiry_date = :expiry, last_updated_by = :updated_by, last_updated_on = :updated_on',
            ExpressionAttributeValues={
                ':expiry': new_expiry,
                ':updated_by': current_username,
                ':updated_on': datetime.now().isoformat()
            }
        )

        return json_response({'message': 'License updated successfully'})

    except Exception as e:
        return json_response({'error': str(e)}, 500)