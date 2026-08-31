"""The client's window onto their own file.

Rebuilt on the core portal idioms rather than beside them. The previous
controller searched without ACLs (a portal user got a 403 on the list page, on
the module's own entry point), compensated on the detail page with sudo (so
any field a template ever added would have leaked, field-group protections
included), compared access tokens with ``==`` (timing-unsafe; core uses
consteq), listed documents that were published but no longer approved, and
returned attachments through make_response(raw) — no mimetype sniffing, no
filename sanitisation, the whole file in memory.

The shape now: record rules in legal_portal_security.xml say what a client may
see, the controller searches as the user, core's _document_check_access
handles the detail page (consteq token fallback included), and the templates
receive curated values — never a raw sudo record to wander through.
"""
from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class LegalCustomerPortal(CustomerPortal):

    # ------------------------------------------------------------------ home
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'legal_case_count' in counters:
            values['legal_case_count'] = (
                request.env['legal.case'].search_count([], limit=1)
                if request.env['legal.case'].has_access('read') else 0)
        return values

    # ------------------------------------------------------------------ list
    def _legal_case_searchbar_sortings(self):
        return {
            'date': {'label': _('Newest'), 'order': 'create_date desc'},
            'name': {'label': _('Reference'), 'order': 'name'},
            'stage': {'label': _('Stage'), 'order': 'stage_id'},
        }

    def _legal_case_searchbar_filters(self):
        return {
            'all': {'label': _('All'), 'domain': []},
            'open': {'label': _('In Progress'), 'domain': [('state', '=', 'confirmed')]},
            'closed': {'label': _('Closed'), 'domain': [('state', '=', 'closed')]},
            # 'cancelled' is deliberately absent: the record rule hides those rows.
        }

    @http.route(['/my/legal-cases', '/my/legal-cases/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_legal_cases(self, page=1, sortby=None, filterby=None, **kw):
        LegalCase = request.env['legal.case']
        # The record rule scopes to the client's own accepted cases; the
        # controller adds nothing to the domain but the user's filter choice.
        searchbar_sortings = self._legal_case_searchbar_sortings()
        searchbar_filters = self._legal_case_searchbar_filters()
        # These keys arrive from the URL: index nothing without a default.
        if sortby not in searchbar_sortings:
            sortby = 'date'
        if filterby not in searchbar_filters:
            filterby = 'all'
        domain = searchbar_filters[filterby]['domain']
        order = searchbar_sortings[sortby]['order']

        case_count = LegalCase.search_count(domain)
        pager = portal_pager(
            url='/my/legal-cases',
            url_args={'sortby': sortby, 'filterby': filterby},
            total=case_count, page=page, step=self._items_per_page)
        cases = LegalCase.search(domain, order=order,
                                 limit=self._items_per_page, offset=pager['offset'])
        request.session['my_legal_cases_history'] = cases.ids[:100]

        values = self._prepare_portal_layout_values()
        values.update({
            'cases': cases,
            'page_name': 'legal_case',
            'pager': pager,
            'default_url': '/my/legal-cases',
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
            'searchbar_filters': searchbar_filters,
            'filterby': filterby,
        })
        return request.render('era_law_firm.portal_my_legal_cases', values)

    # ---------------------------------------------------------------- detail
    def _legal_case_page_values(self, case_sudo, access_token, **kwargs):
        """Curated values for the case page.

        _document_check_access hands back a sudo record; nothing here passes
        it on unfiltered. Children go through the same conditions the portal
        record rules impose, so the logged-in path and the token path show the
        same thing — and a template author cannot reach past what is offered.
        """
        documents = case_sudo.document_ids.filtered(
            lambda d: d.portal_published and d.state == 'approved')
        hearings = case_sudo.hearing_ids.filtered(
            lambda h: h.state in ('confirmed', 'done')).sorted('start_datetime')
        parties = case_sudo.party_ids.filtered('portal_visible')
        # Trust postings also stamp legal_case_id on their journal entries;
        # without the move_type filter the client would see the firm's
        # internal bookkeeping presented as their invoices.
        client_commercial = case_sudo.client_id.commercial_partner_id
        invoices = case_sudo.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted'
            and m.partner_id.commercial_partner_id == client_commercial)
        invoice_rows = [{
            'name': move.name,
            'date': move.invoice_date,
            'amount': move.amount_total,
            'currency': move.currency_id,
            'payment_state': move.payment_state,
            'url': move.get_portal_url(),
        } for move in invoices]

        values = {
            'page_name': 'legal_case',
            'case': case_sudo,
            'documents': documents,
            'hearings': hearings,
            'parties': parties,
            'invoice_rows': invoice_rows,
            # Not the stored aggregate: that one sums every posted movement
            # tagged with the case, whichever client's trust account it sits
            # in. What the page shows is scoped the way the transaction record
            # rule is — this client's own money only.
            'trust_allocated': self._client_trust_allocated(case_sudo, client_commercial),
        }
        return self._get_page_view_values(
            case_sudo, access_token, values, 'my_legal_cases_history', False, **kwargs)

    @staticmethod
    def _client_trust_allocated(case_sudo, client_commercial):
        total = 0.0
        for tx in case_sudo.trust_transaction_ids.filtered(
                lambda t: t.state == 'posted'
                and t.trust_account_id.partner_id.commercial_partner_id == client_commercial):
            total += -tx.amount if tx.transaction_type == 'transfer' else tx.signed_amount
        for tx in case_sudo.trust_transfer_in_ids.filtered(
                lambda t: t.state == 'posted' and t.transaction_type == 'transfer'
                and t.trust_account_id.partner_id.commercial_partner_id == client_commercial):
            total += tx.amount
        return total

    @http.route(['/my/legal-cases/<int:case_id>'],
                type='http', auth='public', website=True)
    def portal_my_legal_case(self, case_id, access_token=None, **kw):
        try:
            case_sudo = self._document_check_access('legal.case', case_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        # The token path skips record rules, so the rule's own conditions are
        # re-stated here: draft is the firm still deciding, cancelled is an
        # engagement that never happened.
        if case_sudo.state in ('draft', 'cancelled'):
            return request.redirect('/my')
        return request.render('era_law_firm.portal_legal_case',
                              self._legal_case_page_values(case_sudo, access_token, **kw))

    # -------------------------------------------------------------- download
    @http.route(['/my/legal-cases/<int:case_id>/documents/<int:document_id>/download'],
                type='http', auth='public', website=True)
    def portal_legal_document_download(self, case_id, document_id, access_token=None, **kw):
        try:
            case_sudo = self._document_check_access('legal.case', case_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        document = case_sudo.document_ids.filtered(
            lambda d: d.id == document_id and d.portal_published
            and d.state == 'approved')[:1]
        if not document or not document.attachment_id or case_sudo.state in ('draft', 'cancelled'):
            return request.not_found()
        return request.env['ir.binary']._get_stream_from(
            document.attachment_id).get_response(as_attachment=True)
