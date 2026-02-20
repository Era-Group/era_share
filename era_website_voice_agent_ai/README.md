# Website Realtime Agent Floating Widget (Odoo 19)

Adds a floating widget to talk with an OpenAI Realtime agent, records calls, and stores
summaries/transcripts/recordings in CRM.

## Install
1. Copy this module folder `era_website_voice_agent_ai` into your Odoo addons path.
2. Restart Odoo.
3. Apps → Update Apps List.
4. Install **Website Realtime Agent Floating Widget**.

## Configure
Settings → (Technical) → OpenAI → Realtime Voice Agent

Fill:
- OpenAI API Key
- Realtime Prompt ID (pmpt_...)
- Realtime Prompt Version (optional)
- Model (default: gpt-realtime-mini; must match your Prompt's model)
- Voice (default: alloy)
- Show Website Widget (toggle)
- Summary Prompt (used for call analysis in Arabic)

## Usage
Open your website, you'll see a floating button bottom-right.
- Click **تكلّم** to connect (browser will ask microphone permission)
- Optional: type and send text from the panel
- When the call ends, the recording is saved in CRM with a summary

## Scope
Recording and AI summary are only supported for calls started from the Realtime Agent Widget.
Standard Odoo VoIP calls and SIP webhook ingestion are intentionally disabled in this module.

## Security Note
The OpenAI API key is stored server-side. The browser only receives a short-lived token via `/realtime_agent/token`.

## External Website Embed
Use this script on any external website:

```html
<script
  src="https://crm.era.net.sa/era_website_voice_agent_ai/static/src/js/realtime_agent_embed_loader.js"
  data-base-url="https://crm.era.net.sa"
  data-model="gpt-realtime-mini"
  data-voice="alloy"
  data-label="تحدث معنا"
  data-right="14"
  data-bottom="14"
  data-z-index="2147483000">
</script>
```

Optional attributes:
- `data-prompt-id`: Override the prompt for this embed only.
- `data-caller-company`: Pre-fill company name in saved summaries.
- `data-closed-width` / `data-closed-height`: Control button-only frame size.

Security:
- In Odoo Settings you can set `Allowed Embed Origins` (one origin per line) to restrict which external websites can load the embed frame.
