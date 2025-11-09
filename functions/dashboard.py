import json
import boto3
from datetime import datetime

def lambda_handler(event, context):
    print("Dashboard Lambda starting...")

    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}

    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': cors_headers(),
            'body': json.dumps({'message': 'CORS preflight'})
        }

    try:
        dynamodb = boto3.resource('dynamodb')
        licenses_table = dynamodb.Table('licenses')
        users_table = dynamodb.Table('users')

        method = event['httpMethod']
        path = event.get('path', '')

        if method == 'GET' and path.endswith('/dashboard'):
            return handle_dashboard(headers, licenses_table, users_table, event)

        return json_response({'error': 'Not found'}, 404)

    except Exception as e:
        print(f"Error: {str(e)}")
        return json_response({'error': 'Internal server error'}, 500)

def handle_dashboard(headers, licenses_table, users_table, event):
    print("Handling dashboard request")

    query_params = event.get('queryStringParameters', {}) or {}
    query = query_params.get('query', '').strip().lower()

    try:
        response = licenses_table.scan()
        all_licenses = response.get('Items', [])
        print(f"Found {len(all_licenses)} licenses")
    except Exception as e:
        return json_response({'error': f'DynamoDB scan error (licenses): {str(e)}'}, 500)

    # Filter licenses by name or either owner's name
    if query:
        licenses = [
            lic for lic in all_licenses
            if query in lic.get('name', '').lower()
            or query in lic.get('primary_owner', '').lower()
            or query in lic.get('secondary_owner', '').lower()
        ]
    else:
        licenses = all_licenses

    today = datetime.today().date()
    expiring_soon = 0
    for lic in licenses:
        expiry_str = lic.get('expiry_date')
        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            if (expiry - today).days <= 30:
                expiring_soon += 1
        except ValueError:
            continue

    try:
        users_response = users_table.scan()
        all_users = users_response.get('Items', [])
        print(f"Found {len(all_users)} users")
    except Exception as e:
        return json_response({'error': f'DynamoDB scan error (users): {str(e)}'}, 500)

    current_username = headers.get('x-username', '')
    users = [user for user in all_users if user.get('username') != current_username]
    admin_count = sum(1 for user in all_users if user.get('role') == 'admin')

    return json_response({
        'licenses': licenses,
        'users': users,
        'expiring_soon': expiring_soon,
        'total_users': len(all_users),
        'admin_count': admin_count
    })

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
        'Access-Control-Allow-Headers': 'Content-Type, X-User-ID, X-Username, X-Role, x-user-id, x-username, x-role, Authorization',
        'Strict-Transport-Security': 'max-age=63072000; includeSubDomains; preload',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY'
    }