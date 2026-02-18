# Website Realtime Agent Floating Widget (Odoo 19)

Adds a floating widget to talk with an OpenAI Realtime agent, records calls, and stores
summaries/transcripts/recordings in CRM. Also integrates with Odoo VoIP to record inbound
and outbound calls from the browser, and can ingest SIP recordings via webhook.

## Install
1. Copy this module folder `era_voip_ai` into your Odoo addons path.
2. Restart Odoo.
3. Apps → Update Apps List.
4. Install **Website Realtime Agent Floating Widget**.

## Configure
Settings → (Technical) → OpenAI → Realtime Voice Agent

Fill:
- OpenAI API Key
- Realtime Prompt ID (pmpt_...)
- Realtime Prompt Version (optional)
- Model (default: gpt-realtime; must match your Prompt's model)
- Voice (default: alloy)
- Show Website Widget (toggle)
- Summary Prompt (used for call analysis in Arabic)

## Usage
Open your website, you'll see a floating button bottom-right.
- Click **تكلّم** to connect (browser will ask microphone permission)
- Optional: type and send text from the panel
- When the call ends, the recording is saved in CRM with a summary

## Odoo VoIP
Inbound and outbound calls handled by Odoo VoIP are recorded in the browser and sent to
the server for transcription/summary storage in CRM. This requires the VoIP app to be
installed and configured.

## SIP Trunk (Odoo VoIP + Asterisk)
For inbound calls via SIP, use Odoo's VoIP with a local Asterisk server and the
SIP trunk settings added by this module. See `era_voip_ai/docs/SIP_TRUNK_SETUP.md`.

## Security Note
The OpenAI API key is stored server-side. The browser only receives a short-lived token via `/realtime_agent/token`.
