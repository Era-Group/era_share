from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "era_voip_ext")
class TestTranscriptOutput(TransactionCase):
    def test_text_account_accepts_cli_but_rejects_assemblyai(self):
        Account = self.env["era.ai.account"]
        cli = Account.create({
            "name": "Text CLI", "provider": "openai", "auth_mode": "cli_proxy",
        })
        assembly = Account.create({
            "name": "Speech only", "provider": "assemblyai", "auth_mode": "api_key",
        })
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("era_voip_ext.text_account_id", str(cli.id))
        self.assertEqual(self.env["voip.call"]._text_account(), cli)
        icp.set_param("era_voip_ext.text_account_id", str(assembly.id))
        self.assertNotEqual(self.env["voip.call"]._text_account(), assembly)

    def test_transcription_account_accepts_assemblyai_but_rejects_cli(self):
        Account = self.env["era.ai.account"]
        assembly = Account.create({
            "name": "Speech", "provider": "assemblyai", "auth_mode": "api_key",
        })
        cli = Account.create({
            "name": "No speech", "provider": "openai", "auth_mode": "cli_proxy",
        })
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("era_voip_ext.transcription_account_id", str(assembly.id))
        self.assertEqual(self.env["voip.call"]._transcription_account(), assembly)
        icp.set_param("era_voip_ext.transcription_account_id", str(cli.id))
        self.assertNotEqual(self.env["voip.call"]._transcription_account(), cli)

    def test_complete_role_output_replaces_provider_transcript(self):
        raw = (
            "Speaker 1: السلام عليكم، معك هبة من شركة إيرا.\n"
            "Speaker 2: وعليكم السلام، تفضلي."
        )
        formatted = (
            "الموظف: السلام عليكم، معك هبة من شركة إيرا.\n"
            "العميل: وعليكم السلام، تفضلي."
        )
        result = self.env["voip.call"]._select_transcript_output(raw, formatted)
        self.assertEqual(result, formatted)
        self.assertNotIn("------------", result)
        self.assertNotIn("Speaker 1", result)

    def test_placeholder_falls_back_to_provider_transcript(self):
        raw = "Speaker 1: السلام عليكم\nSpeaker 2: وعليكم السلام"
        self.assertEqual(
            self.env["voip.call"]._select_transcript_output(raw, "..."), raw)

    def test_missing_role_falls_back_to_provider_transcript(self):
        raw = "Speaker 1: السلام عليكم\nSpeaker 2: وعليكم السلام"
        formatted = "الموظف: السلام عليكم\nالموظف: وعليكم السلام"
        self.assertEqual(
            self.env["voip.call"]._select_transcript_output(raw, formatted), raw)

    def test_truncated_role_output_falls_back_to_provider_transcript(self):
        raw = (
            "Speaker 1: هذا نص طويل للمكالمة يحتوي على تفاصيل العرض والمتابعة "
            "والموعد المقترح والخطوات التالية المطلوبة من الطرفين.\n"
            "Speaker 2: شكرا، سأراجع العرض وأرسل الرد بعد الاجتماع القادم."
        )
        formatted = "الموظف: مرحبا\nالعميل: شكرا"
        self.assertEqual(
            self.env["voip.call"]._select_transcript_output(raw, formatted), raw)
