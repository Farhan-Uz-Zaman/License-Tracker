License Tracker is a simple, secure, and modular application designed to manage software licenses efficiently. It combines the power of a traditional Flask backend with the scalability of AWS Lambda and the flexibility of AWS Amplify for frontend deployment.

Features
• 	Flask Backend: A robust RESTful API for managing license data, user authentication, and admin operations.
• 	Lambda Functions: Lightweight serverless modules for handling specific tasks like license validation, email notifications, and usage tracking.
• 	Amplify Frontend: A responsive web interface built for ease of use and fast deployment.

Project Structure
/LicenseTracker
├── app/       # Flask app
├── functions/       # AWS Lambda functions
├── amplify/      # Amplify-hosted frontend
├── .gitignore
├── README.md
└── requirements.txt

Role-based access control

Tech Stack
• 	Backend: Flask, Python
• 	Serverless: AWS Lambda, API Gateway
• 	Frontend: HTML/CSS/JS  hosted on AWS Amplify
• 	Database: "DynamoDB" for serverless deployment, "SQLite" for container

