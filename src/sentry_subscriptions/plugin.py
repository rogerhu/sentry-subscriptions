from django import forms
from django.core.validators import email_re
from django.core.validators import ValidationError
from django.utils.translation import ugettext_lazy as _
from sentry.plugins.sentry_mail.models import MailPlugin
from sentry.utils.email import MessageBuilder

import fnmatch
import sentry_subscriptions


class SubscriptionField(forms.CharField):
    '''Custom field for converting stored dictionary value to TextArea string'''

    def prepare_value(self, value):
        '''Convert dict to string'''

        if isinstance(value, dict):
            value = self.to_text(value)

        return value

    def to_text(self, value):

        subscription_lines = []
        for pattern, emails in value.iteritems():
            subscription_lines.append('%s %s' % (pattern, ','.join(emails)))

        return '\n'.join(subscription_lines)


class SubscriptionOptionsForm(forms.Form):
    subscriptions = SubscriptionField(label=_('Subscriptions'),
        widget=forms.Textarea(attrs={'class': 'span6', 'placeholder': 'module.submodule.* example@domain.com,foo@bar.com'}),
        help_text=_('Enter one subscription per line in the format of <module patter> <notification emails>.'))

    def clean_subscriptions(self):

        value = self.cleaned_data['subscriptions']
        subscription_lines = value.strip().splitlines()
        subscriptions = {}

        for subscription_line in subscription_lines:
            tokens = subscription_line.split(' ')
            if len(tokens) != 2:
                raise ValidationError('Invalid subscription specification: %s. Must specify a module pattern and list of emails' % subscription_line)

            pattern = self.clean_pattern(tokens[0])
            emails = self.clean_emails(tokens[1])

            if pattern in subscriptions:
                raise ValidationError('Duplicate subscription: %s' % subscription_line)

            subscriptions[pattern] = emails

        return subscriptions

    def clean_pattern(self, pattern):
        return pattern

    def clean_emails(self, emails):
        email_values = emails.split(',')

        for email in email_values:
            if not email_re.match(email):
                raise ValidationError('%s is not a valid email address' % email)

        return email_values


class SubscriptionsPlugin(MailPlugin):

    author = 'John Lynn'
    author_url = 'https://github.com/jlynn/sentry-subscriptions'
    version = sentry_subscriptions.VERSION
    description = 'Enable email subscriptions to exceptions'

    slug = 'subscriptions'
    title = _('Subscriptions')
    conf_title = title
    conf_key = 'subscriptions'
    project_conf_form = SubscriptionOptionsForm

    subject_prefix = "[Sentry Subscription] "

    def is_configured(self, project, **kwargs):
        return bool(self.get_option('subscriptions', project))

    def should_notify(self, event, is_new):

        if is_new:
            return True

        if event.group:
            count = event.group.times_seen
            if count <= 100 and count % 10 == 0:
                return True
            if count <= 1000 and count % 100 == 0:
                return True
            elif count % 1000 == 0:
                return True

        return False

    def get_matches(self, event):
        subscriptions = self.get_option('subscriptions', event.project)

        notifications = []

        for pattern, emails in subscriptions.iteritems():
            if fnmatch.fnmatch(event.culprit, pattern):
                    notifications += emails

        return notifications

    def _send_mail(self, subject, template=None, html_template=None, body=None,
                   project=None, headers=None, context=None, fail_silently=False):

        subject_prefix = self.get_option('subject_prefix', project) or self.subject_prefix

        msg = MessageBuilder(
            subject='%s%s' % (subject_prefix, subject),
            template=template,
            html_template=html_template,
            body=body,
            headers=headers,
            context=context,
        )

        send_to = self._send_to

        return msg.send(to=send_to, fail_silently=fail_silently)

    def post_process(self, group, event, is_new, is_sample, **kwargs):

        if not event.culprit:
            return

        if not self.is_configured(group.project):
            return

        if self.should_notify(event, is_new):
            try:
                self._send_to = self.get_matches(event)
                self.notify_users(group, event)
            finally:
                self._send_to = []
