# WhatsApp Review Management System

A WhatsApp-based system for restaurant managers to approve and revise AI-generated responses to customer reviews.

## Features

- Fetch restaurant reviews automatically
- AI-powered response generation
- WhatsApp-based review approval workflow
- Manager feedback and response revision
- MongoDB storage for reviews and responses

## Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file with required environment variables
4. Run the server: `python whatsapp_server.py`

## Environment Variables

- `GOOGLE_API_KEY`: Google API key for Gemini model
- `TWILIO_ACCOUNT_SID`: Your Twilio account SID
- `TWILIO_AUTH_TOKEN`: Your Twilio auth token
- `TWILIO_WHATSAPP_NUMBER`: Your Twilio WhatsApp number
- `MONGODB_URI`: MongoDB connection string
- `MONGODB_DB_NAME`: MongoDB database name