"""Template registry — every business event, in en/hi/gu (DEC-10, BR-NOT-07/08).

WhatsApp bodies double as SMS bodies until DLT approval maps provider ids.
Variables use {{name}} and are validated at render time — an unresolved
variable is an error, never silently sent.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationFailed
from app.modules.notifications.models import NotificationTemplate

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# key -> {lang: body}. Transactional unless noted.
TEMPLATES: dict[str, dict[str, str]] = {
    "order.placed": {
        "en": "Liger: Order {{order_no}} received — {{item_count}} item(s), total {{total}}. Expected delivery {{expected_delivery}}. Thank you!",
        "hi": "Liger: आपका ऑर्डर {{order_no}} मिल गया है — {{item_count}} आइटम, कुल {{total}}। अनुमानित डिलीवरी {{expected_delivery}}। धन्यवाद!",
        "gu": "Liger: તમારો ઓર્ડર {{order_no}} મળ્યો છે — {{item_count}} વસ્તુ, કુલ {{total}}. અંદાજિત ડિલિવરી {{expected_delivery}}. આભાર!",
    },
    "order.confirmed": {
        "en": "Liger: Order {{order_no}} confirmed. Total {{total}}. We will update you at every stage.",
        "hi": "Liger: ऑर्डर {{order_no}} कन्फर्म हो गया। कुल {{total}}। हर स्टेज पर हम आपको अपडेट देंगे।",
        "gu": "Liger: ઓર્ડર {{order_no}} કન્ફર્મ થયો. કુલ {{total}}. દરેક તબક્કે અમે તમને અપડેટ આપીશું.",
    },
    "order.status": {
        "en": "Liger: Order {{order_no}} is now {{status}}.{{extra}}",
        "hi": "Liger: ऑर्डर {{order_no}} अब {{status}} है।{{extra}}",
        "gu": "Liger: ઓર્ડર {{order_no}} હવે {{status}} છે.{{extra}}",
    },
    "invoice.issued": {
        "en": "Liger: Invoice {{invoice_no}} of {{total}} issued. Due date {{due_date}}. Pay: {{pay_link}}",
        "hi": "Liger: {{total}} का इनवॉइस {{invoice_no}} जारी हुआ। देय तिथि {{due_date}}। भुगतान: {{pay_link}}",
        "gu": "Liger: {{total}} નું ઇન્વોઇસ {{invoice_no}} જારી થયું. ચૂકવણી તારીખ {{due_date}}. ચૂકવો: {{pay_link}}",
    },
    "credit.pre_due": {
        "en": "Liger: Gentle reminder — {{amount}} is due on {{due_date}} (invoice {{invoice_no}}). Pay easily: {{pay_link}}",
        "hi": "Liger: नम्र स्मरण — {{amount}} का भुगतान {{due_date}} तक देय है (इनवॉइस {{invoice_no}})। आसान भुगतान: {{pay_link}}",
        "gu": "Liger: નમ્ર યાદ — {{amount}} ની ચૂકવણી {{due_date}} સુધી બાકી છે (ઇન્વોઇસ {{invoice_no}}). સરળ ચૂકવણી: {{pay_link}}",
    },
    "credit.due_today": {
        "en": "Liger: {{amount}} is due TODAY (invoice {{invoice_no}}). Pay now: {{pay_link}}",
        "hi": "Liger: {{amount}} का भुगतान आज देय है (इनवॉइस {{invoice_no}})। अभी भुगतान करें: {{pay_link}}",
        "gu": "Liger: {{amount}} ની ચૂકવણી આજે બાકી છે (ઇન્વોઇસ {{invoice_no}}). હમણાં ચૂકવો: {{pay_link}}",
    },
    "credit.warn1": {
        "en": "Liger: Invoice {{invoice_no}} of {{amount}} is {{days_overdue}} days overdue. Please clear it to keep ordering smoothly: {{pay_link}}",
        "hi": "Liger: इनवॉइस {{invoice_no}} ({{amount}}) {{days_overdue}} दिन से बकाया है। ऑर्डर जारी रखने के लिए कृपया भुगतान करें: {{pay_link}}",
        "gu": "Liger: ઇન્વોઇસ {{invoice_no}} ({{amount}}) {{days_overdue}} દિવસથી બાકી છે. ઓર્ડર ચાલુ રાખવા કૃપા કરી ચૂકવણી કરો: {{pay_link}}",
    },
    "credit.warn2_final": {
        "en": "Liger FINAL NOTICE: Invoice {{invoice_no}} of {{amount}} is {{days_overdue}} days overdue. Your account will be BLOCKED for new orders in {{days_to_block}} days. Pay now: {{pay_link}}",
        "hi": "Liger अंतिम सूचना: इनवॉइस {{invoice_no}} ({{amount}}) {{days_overdue}} दिन से बकाया है। {{days_to_block}} दिनों में नए ऑर्डर के लिए आपका खाता ब्लॉक हो जाएगा। अभी भुगतान करें: {{pay_link}}",
        "gu": "Liger અંતિમ સૂચના: ઇન્વોઇસ {{invoice_no}} ({{amount}}) {{days_overdue}} દિવસથી બાકી છે. {{days_to_block}} દિવસમાં નવા ઓર્ડર માટે તમારું ખાતું બ્લોક થશે. હમણાં ચૂકવો: {{pay_link}}",
    },
    "credit.blocked": {
        "en": "Liger: Your account is blocked for new orders. Outstanding: {{outstanding}}. Clear your dues to resume instantly: {{pay_link}}",
        "hi": "Liger: नए ऑर्डर के लिए आपका खाता ब्लॉक है। बकाया: {{outstanding}}। तुरंत शुरू करने के लिए बकाया चुकाएँ: {{pay_link}}",
        "gu": "Liger: નવા ઓર્ડર માટે તમારું ખાતું બ્લોક છે. બાકી: {{outstanding}}. તરત શરૂ કરવા બાકી ચૂકવો: {{pay_link}}",
    },
    "credit.unblocked": {
        "en": "Liger: Thank you! Payment received and your account is active again. Available credit: {{available}}.",
        "hi": "Liger: धन्यवाद! भुगतान मिल गया और आपका खाता फिर सक्रिय है। उपलब्ध क्रेडिट: {{available}}।",
        "gu": "Liger: આભાર! ચૂકવણી મળી ગઈ અને તમારું ખાતું ફરી સક્રિય છે. ઉપલબ્ધ ક્રેડિટ: {{available}}.",
    },
    "payment.received": {
        "en": "Liger: Payment of {{amount}} received ({{method}}). New outstanding: {{outstanding}}. Thank you!",
        "hi": "Liger: {{amount}} का भुगतान मिला ({{method}})। नया बकाया: {{outstanding}}। धन्यवाद!",
        "gu": "Liger: {{amount}} ની ચૂકવણી મળી ({{method}}). નવું બાકી: {{outstanding}}. આભાર!",
    },
    "payment.cash_pending": {  # to ADMIN (BR-PAY-05)
        "en": "Liger ADMIN: {{staff}} recorded CASH {{amount}} from {{customer}}. Confirm in the cash queue to release credit.",
        "hi": "Liger ADMIN: {{staff}} ने {{customer}} से नकद {{amount}} दर्ज किया। क्रेडिट जारी करने के लिए कैश क्यू में कन्फर्म करें।",
        "gu": "Liger ADMIN: {{staff}} એ {{customer}} પાસેથી રોકડ {{amount}} નોંધી. ક્રેડિટ છોડવા કેશ ક્યુમાં કન્ફર્મ કરો.",
    },
    "admin.daily_digest": {
        "en": "Liger daily: {{orders_count}} orders ({{orders_value}}), collections {{collections}}, new blocks: {{new_blocks}}. Outstanding: {{outstanding}}.",
        "hi": "Liger दैनिक: {{orders_count}} ऑर्डर ({{orders_value}}), वसूली {{collections}}, नए ब्लॉक: {{new_blocks}}। कुल बकाया: {{outstanding}}।",
        "gu": "Liger દૈનિક: {{orders_count}} ઓર્ડર ({{orders_value}}), વસૂલાત {{collections}}, નવા બ્લોક: {{new_blocks}}. કુલ બાકી: {{outstanding}}.",
    },
}

LANGUAGES = ("en", "hi", "gu")


def seed_templates(db: Session) -> None:
    """Idempotent. WhatsApp + SMS get the same body until DLT ids differ."""
    existing = {
        (t.key, t.channel, t.language)
        for t in db.query(NotificationTemplate.key, NotificationTemplate.channel,
                          NotificationTemplate.language).all()
    }
    for key, langs in TEMPLATES.items():
        for lang, body in langs.items():
            for channel in ("whatsapp", "sms"):
                if (key, channel, lang) not in existing:
                    db.add(NotificationTemplate(key=key, channel=channel, language=lang,
                                                body=body))
    db.commit()


def render(db: Session, key: str, channel: str, language: str,
           variables: dict[str, str]) -> tuple[str, NotificationTemplate]:
    """BR-NOT-08: unresolved variables are an error, never sent."""
    template = (
        db.query(NotificationTemplate)
        .filter(NotificationTemplate.key == key,
                NotificationTemplate.channel == channel,
                NotificationTemplate.language == language)
        .first()
    )
    if template is None:  # fall back to English rather than silence
        template = (
            db.query(NotificationTemplate)
            .filter(NotificationTemplate.key == key,
                    NotificationTemplate.channel == channel,
                    NotificationTemplate.language == "en")
            .first()
        )
    if template is None:
        raise ValidationFailed(f"No template for key '{key}' on {channel}")

    body = template.body
    for name, value in variables.items():
        body = body.replace("{{" + name + "}}", str(value))
    leftover = _VAR_RE.findall(body)
    if leftover:
        raise ValidationFailed(
            f"Template '{key}' missing variables: {', '.join(sorted(set(leftover)))}"
        )
    return body, template
