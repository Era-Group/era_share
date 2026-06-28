# WAHA WhatsApp Integration for Odoo 18

This module provides WhatsApp integration for Odoo using the WAHA (WhatsApp HTTP API) service.

## Features

- **Session Management**: Create and manage multiple WhatsApp sessions
- **QR Code Integration**: Scan QR codes directly from Odoo interface
- **Message Sending**: Send text messages, files, and voice messages
- **Template System**: Create and use message templates
- **Contact Integration**: Link WhatsApp with Odoo contacts
- **Company Settings**: Set default WhatsApp session per company
- **Message History**: Track all sent and received messages

## Requirements

- Odoo 18.0
- WAHA server (running via Docker)
- Python requests library

## Installation

1. **Setup WAHA Server**:
   ```bash
   # Start WAHA server
   docker run -it -p 3000:3000 devlikeapro/waha
   
   # Or with GOWS engine (recommended)
   docker run -it -e "WHATSAPP_DEFAULT_ENGINE=GOWS" -p 3000:3000 devlikeapro/waha
   ```

2. **Install Module**:
   - Copy the module folder to your Odoo addons directory
   - Update the app list in Odoo
   - Install the "WAHA WhatsApp Integration" module

3. **Configure**:
   - Go to WhatsApp > Sessions
   - Create a new session
   - Configure WAHA server URL (default: http://localhost:3000)
   - Start session and scan QR code

## Usage

### Creating a Session

1. Navigate to **WhatsApp > Sessions > Manage Sessions**
2. Click **Create** and fill in:
   - Session Name
   - WAHA Server URL
   - Engine (GOWS recommended)
3. Click **Start Session**
4. Scan the QR code with your WhatsApp mobile app

### Sending Messages

#### From Contacts:
1. Open any contact
2. Set the WhatsApp Number field
3. Click the **Send WhatsApp** button

#### From WhatsApp Menu:
1. Go to **WhatsApp > Messages > Send Message**
2. Fill in the recipient details
3. Choose message type (text, file, or voice)
4. Send the message

### Using Templates

1. Create templates in **WhatsApp > Templates > Message Templates**
2. Use templates when sending messages for quick access to common messages

### Company Configuration

1. Go to **Settings > Companies > Companies**
2. Edit your company
3. In the **WhatsApp** tab, set the default session

## API Integration

The module provides the following key methods:

```python
# Start a session
session.action_start_session()

# Send text message
session.send_message(chat_id, text)

# Send file
session.send_message(chat_id, text, file_data, file_name, file_type)

# Send voice message
session.send_voice_message(chat_id, voice_data)
```

## Configuration Files Structure

```
sadeem_waha_whatsapp/
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── waha_session.py
│   ├── res_company.py
│   ├── whatsapp_message.py
│   ├── whatsapp_template.py
│   └── res_partner.py
├── wizard/
│   ├── __init__.py
│   └── send_whatsapp_wizard.py
├── views/
│   ├── waha_session_views.xml
│   ├── res_company_views.xml
│   ├── whatsapp_message_views.xml
│   ├── whatsapp_template_views.xml
│   ├── res_partner_views.xml
│   ├── send_whatsapp_wizard_views.xml
│   └── menu.xml
├── security/
│   ├── ir.model.access.csv
│   └── security.xml
├── data/
│   └── data.xml
├── static/
│   ├── src/
│   │   ├── css/waha_style.css
│   │   ├── js/qr_code_widget.js
│   │   └── xml/qr_code_templates.xml
│   └── description/
│       └── icon.png
└── README.md
```

## Troubleshooting

### Common Issues:

1. **Session won't start**: Check WAHA server URL and ensure WAHA is running
2. **QR Code not showing**: Refresh the session status and try getting QR code again
3. **Messages not sending**: Verify session status is "Working"
4. **Connection errors**: Check firewall settings and network connectivity

### Logs:
Check Odoo logs for detailed error messages:
```bash
tail -f /var/log/odoo/odoo.log | grep -i waha
```

## Support

For issues and questions:
- https://sadeem.cloud/helpdesk/customer-care-1

## License

This module is licensed under OPL-1.