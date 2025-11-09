import json
import boto3

# Initialize DynamoDB tables
dynamodb = boto3.resource('dynamodb')
users_table = dynamodb.Table('users')
licenses_table = dynamodb.Table('licenses')

def lambda_handler(event, context):
    # Handle CORS preflight
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': cors_headers(),
            'body': json.dumps({'message': 'CORS preflight'})
        }

    # Block deleted users globally
    user_id, _, _ = get_current_user(event)
    if not user_id:
        return json_response({'error': 'User no longer exists'}, 403)

    method = event['httpMethod']
    path = event.get('path', '')

    if path.startswith('/admin/users/') and path.endswith('/promote') and method == 'POST':
        return promote_user(event)
    elif path.startswith('/admin/users/') and path.endswith('/transfer_admin') and method == 'POST':
        return transfer_admin(event)
    elif path.startswith('/admin/users/') and method == 'DELETE':
        return delete_user(event)
    elif path.startswith('/admin/licenses/') and method == 'DELETE':
        return delete_license(event)

    return json_response({'error': 'Not found'}, 404)

def get_current_user(event):
    headers = event.get('headers', {})
    user_id = headers.get('x-user-id') or headers.get('X-User-ID')
    username = headers.get('x-username') or headers.get('X-Username')
    role = headers.get('x-role') or headers.get('X-Role')

    # Check if user exists in DynamoDB
    if user_id:
        response = users_table.get_item(Key={'user_id': user_id})
        if 'Item' not in response:
            print(f"User ID {user_id} not found in users table.")
            return None, None, None

    return user_id, username, role

def promote_user(event):
    try:
        current_user_id, current_username, current_role = get_current_user(event)
        if current_role != 'admin':
            return json_response({'error': 'Admin access required'}, 403)

        target_user_id = event.get('pathParameters', {}).get('id')
        if not target_user_id:
            return json_response({'error': 'Missing id in path'}, 400)

        response = users_table.get_item(Key={'user_id': target_user_id})
        if 'Item' not in response:
            return json_response({'error': 'User not found'}, 404)

        all_users = users_table.scan().get('Items', [])
        admin_count = sum(1 for u in all_users if u.get('role') == 'admin')
        if admin_count >= 3:
            return json_response({'error': 'Maximum number of admins reached'}, 403)

        users_table.update_item(
            Key={'user_id': target_user_id},
            UpdateExpression='SET #r = :role',
            ExpressionAttributeNames={'#r': 'role'},
            ExpressionAttributeValues={':role': 'admin'}
        )

        return json_response({'message': 'User promoted to admin'})
    except Exception as e:
        return json_response({'error': str(e)}, 500)

def transfer_admin(event):
    try:
        current_user_id, current_username, current_role = get_current_user(event)
        print("Current user ID:", current_user_id)
        print("Current role:", current_role)

        if current_role != 'admin':
            return json_response({'error': 'Admin access required'}, 403)

        new_admin_id = event.get('pathParameters', {}).get('id')
        print("New admin ID:", new_admin_id)

        if not new_admin_id:
            return json_response({'error': 'Missing id in path'}, 400)

        new_admin_response = users_table.get_item(Key={'user_id': new_admin_id})
        current_user_response = users_table.get_item(Key={'user_id': current_user_id})

        if 'Item' not in new_admin_response or 'Item' not in current_user_response:
            return json_response({'error': 'User not found'}, 404)

        # Promote new admin
        users_table.update_item(
            Key={'user_id': new_admin_id},
            UpdateExpression='SET #r = :role',
            ExpressionAttributeNames={'#r': 'role'},
            ExpressionAttributeValues={':role': 'admin'}
        )
        print("New admin promoted.")

        # Demote current admin
        users_table.update_item(
            Key={'user_id': current_user_id},
            UpdateExpression='SET #r = :role',
            ExpressionAttributeNames={'#r': 'role'},
            ExpressionAttributeValues={':role': 'general'}
        )
        print("Current admin demoted.")

        return json_response({'message': 'Admin role transferred successfully'})
    except Exception as e:
        print("Error during transfer_admin:", str(e))
        return json_response({'error': str(e)}, 500)

def delete_user(event):
    try:
        current_user_id, current_username, current_role = get_current_user(event)
        if current_role != 'admin':
            return json_response({'error': 'Admin access required'}, 403)

        target_user_id = event.get('pathParameters', {}).get('id')
        if not target_user_id:
            return json_response({'error': 'Missing id in path'}, 400)
        if target_user_id == current_user_id:
            return json_response({'error': "You can't delete yourself"}, 403)

        response = users_table.get_item(Key={'user_id': target_user_id})
        if 'Item' not in response:
            return json_response({'error': 'User not found'}, 404)

        users_table.delete_item(Key={'user_id': target_user_id})
        return json_response({'message': 'User deleted successfully'})
    except Exception as e:
        return json_response({'error': str(e)}, 500)

def delete_license(event):
    try:
        current_user_id, current_username, current_role = get_current_user(event)
        if current_role != 'admin':
            return json_response({'error': 'Admin access required'}, 403)

        license_id = event.get('pathParameters', {}).get('id')
        if not license_id:
            return json_response({'error': 'Missing id in path'}, 400)

        response = licenses_table.get_item(Key={'license_id': license_id})
        if 'Item' not in response:
            return json_response({'error': 'License not found'}, 404)

        licenses_table.delete_item(Key={'license_id': license_id})
        return json_response({'message': 'License deleted successfully'})
    except Exception as e:
        return json_response({'error': str(e)}, 500)

def json_response(data, status_code=200):
    return {
        'statusCode': status_code,
        'headers': cors_headers(),
        'body': json.dumps(data, default=str)
    }

def cors_headers():
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-User-ID, X-Username, X-Role, x-user-id, x-username, x-role, Authorization'
    }