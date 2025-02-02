# -*- coding: utf-8 -*-
#
# Copyright © 2012 - 2019 Michal Čihař <michal@cihar.com>
#
# This file is part of Weblate <https://weblate.org/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

"""
Tests for user handling.
"""

from __future__ import unicode_literals

from copy import deepcopy

from django.conf import settings
from django.core import mail
from django.test import SimpleTestCase
from django.test.utils import override_settings

from weblate.accounts.models import AuditLog, Profile, Subscription
from weblate.accounts.notifications import (
    FREQ_DAILY,
    FREQ_INSTANT,
    FREQ_MONTHLY,
    FREQ_NONE,
    FREQ_WEEKLY,
    SCOPE_ADMIN,
    SCOPE_COMPONENT,
    SCOPE_DEFAULT,
    SCOPE_PROJECT,
    MergeFailureNotification,
)
from weblate.accounts.tasks import (
    notify_change,
    notify_daily,
    notify_monthly,
    notify_weekly,
    send_mails,
)
from weblate.auth.models import User
from weblate.lang.models import Language
from weblate.trans.models import Alert, Change, Comment, Suggestion, WhiteboardMessage
from weblate.trans.tests.test_views import RegistrationTestMixin, ViewTestCase

TEMPLATES_RAISE = deepcopy(settings.TEMPLATES)
TEMPLATES_RAISE[0]['OPTIONS']['string_if_invalid'] = 'TEMPLATE_BUG[%s]'


@override_settings(TEMPLATES=TEMPLATES_RAISE)
class NotificationTest(ViewTestCase, RegistrationTestMixin):
    def setUp(self):
        super(NotificationTest, self).setUp()
        self.user.email = 'noreply+notify@weblate.org'
        self.user.save()
        czech = Language.objects.get(code='cs')
        profile = Profile.objects.get(user=self.user)
        profile.watched.add(self.project)
        profile.languages.add(czech)
        profile.save()
        notifications = (
            'MergeFailureNotification',
            'ParseErrorNotification',
            'NewStringNotificaton',
            'NewContributorNotificaton',
            'NewSuggestionNotificaton',
            'NewCommentNotificaton',
            'NewComponentNotificaton',
            'ChangedStringNotificaton',
            'NewTranslationNotificaton',
            'MentionCommentNotificaton',
            'LastAuthorCommentNotificaton',
        )
        for notification in notifications:
            Subscription.objects.create(
                user=self.user,
                scope=SCOPE_DEFAULT,
                notification=notification,
                frequency=FREQ_INSTANT,
            )

    @staticmethod
    def second_user():
        return User.objects.create_user(
            'seconduser',
            'noreply+second@example.org',
            'testpassword'
        )

    def validate_notifications(self, count, subject):
        for message in mail.outbox:
            self.assertNotIn('TEMPLATE_BUG', message.subject)
            self.assertNotIn('TEMPLATE_BUG', message.body)
            self.assertNotIn('TEMPLATE_BUG', message.alternatives[0][0])
            if subject:
                self.assertEqual(message.subject, subject)
        self.assertEqual(len(mail.outbox), count)

    def test_notify_merge_failure(self):
        change = Change.objects.create(
            component=self.component,
            details={
                'error': 'Failed merge',
                'status': 'Error\nstatus',
            },
            action=Change.ACTION_FAILED_MERGE,
        )

        # Check mail
        self.assertEqual(len(mail.outbox), 1)

        # Add project owner
        self.component.project.add_user(self.second_user(), '@Administration')
        notify_change(change.pk)

        # Check mail
        self.validate_notifications(2, '[Weblate] Merge failure in Test/Test')

    def test_notify_parse_error(self):
        change = Change.objects.create(
            translation=self.get_translation(),
            details={
                'error': 'Failed merge',
                'filename': 'test/file.po',
            },
            action=Change.ACTION_PARSE_ERROR,
        )

        # Check mail
        self.assertEqual(len(mail.outbox), 1)

        # Add project owner
        self.component.project.add_user(self.second_user(), '@Administration')
        notify_change(change.pk)

        # Check mail
        self.validate_notifications(3, '[Weblate] Parse error in Test/Test')

    def test_notify_new_string(self):
        Change.objects.create(
            translation=self.get_translation(),
            action=Change.ACTION_NEW_STRING,
        )

        # Check mail
        self.validate_notifications(
            1,
            '[Weblate] New string to translate in Test/Test - Czech'
        )

    def test_notify_new_translation(self):
        Change.objects.create(
            unit=self.get_unit(),
            user=self.second_user(),
            old='',
            action=Change.ACTION_CHANGE,
        )

        # Check mail
        self.validate_notifications(
            1,
            '[Weblate] New translation in Test/Test - Czech'
        )

    def test_notify_new_language(self):
        second_user = self.second_user()
        change = Change.objects.create(
            user=second_user,
            component=self.component,
            details={'language': 'de'},
            action=Change.ACTION_REQUESTED_LANGUAGE,
        )

        # Check mail
        self.assertEqual(len(mail.outbox), 1)

        # Add project owner
        self.component.project.add_user(second_user, '@Administration')
        notify_change(change.pk)

        # Check mail
        self.validate_notifications(
            2,
            '[Weblate] New language request in Test/Test'
        )

    def test_notify_new_contributor(self):
        Change.objects.create(
            unit=self.get_unit(),
            user=self.second_user(),
            action=Change.ACTION_NEW_CONTRIBUTOR,
        )

        # Check mail
        self.validate_notifications(
            1,
            '[Weblate] New contributor in Test/Test - Czech'
        )

    def test_notify_new_suggestion(self):
        unit = self.get_unit()
        Change.objects.create(
            unit=unit,
            suggestion=Suggestion.objects.create(
                content_hash=unit.content_hash,
                project=unit.translation.component.project,
                language=unit.translation.language,
                target='Foo'
            ),
            user=self.second_user(),
            action=Change.ACTION_SUGGESTION,
        )

        # Check mail
        self.validate_notifications(
            1,
            '[Weblate] New suggestion in Test/Test - Czech'
        )

    def test_notify_new_comment(self, expected=1, comment='Foo'):
        unit = self.get_unit()
        Change.objects.create(
            unit=unit,
            comment=Comment.objects.create(
                content_hash=unit.content_hash,
                project=unit.translation.component.project,
                comment=comment,
            ),
            user=self.second_user(),
            action=Change.ACTION_COMMENT,
        )

        # Check mail
        self.validate_notifications(
            expected, '[Weblate] New comment in Test/Test'
        )

    def test_notify_new_comment_report(self):
        self.component.report_source_bugs = 'noreply@weblate.org'
        self.component.save()
        self.test_notify_new_comment(2)

    def test_notify_new_comment_mention(self):
        self.test_notify_new_comment(
            2,
            'Hello @{} and @invalid'.format(self.anotheruser.username)
        )

    def test_notify_new_comment_author(self):
        self.edit_unit('Hello, world!\n', 'Ahoj svete!\n')
        change = self.get_unit().change_set.content().order_by('-timestamp')[0]
        change.user = self.anotheruser
        change.save()
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox = []
        self.test_notify_new_comment(2)

    def test_notify_new_component(self):
        Change.objects.create(
            component=self.component,
            action=Change.ACTION_CREATE_COMPONENT
        )
        self.validate_notifications(
            1, '[Weblate] New translation component Test/Test'
        )

    def test_notify_new_whiteboard(self):
        WhiteboardMessage.objects.create(
            component=self.component,
            message='Hello word',
        )
        self.validate_notifications(
            1, '[Weblate] New whiteboard message on Test'
        )
        mail.outbox = []
        WhiteboardMessage.objects.create(
            message='Hello global word',
        )
        self.validate_notifications(
            2, '[Weblate] New whiteboard message at Weblate'
        )

    def test_notify_alert(self):
        self.component.project.add_user(self.user, '@Administration')
        Alert.objects.create(
            component=self.component,
            name='PushFailure',
            details={'error': 'Some error'}
        )
        self.validate_notifications(
            1, '[Weblate] New alert on Test/Test'
        )

    def test_notify_account(self):
        request = self.get_request()
        AuditLog.objects.create(request.user, request, 'password')
        self.assertEqual(len(mail.outbox), 1)
        self.assert_notify_mailbox(mail.outbox[0])

    def test_notify_html_language(self):
        self.user.profile.language = 'cs'
        self.user.profile.save()
        request = self.get_request()
        AuditLog.objects.create(request.user, request, 'password')
        self.assertEqual(len(mail.outbox), 1)
        # There is just one (html) alternative
        content = mail.outbox[0].alternatives[0][0]
        self.assertIn('lang="cs"', content)
        self.assertIn('změněno', content)

    def test_digest(self, frequency=FREQ_DAILY, notify=notify_daily,
                    change=Change.ACTION_FAILED_MERGE, subj='Merge failure'):
        Subscription.objects.filter(
            frequency=FREQ_INSTANT,
            notification__in=(
                'MergeFailureNotification', 'NewTranslationNotificaton'
            ),
        ).update(
            frequency=frequency
        )
        Change.objects.create(
            component=self.component,
            details={
                'error': 'Failed merge',
                'status': 'Error\nstatus',
                'language': 'de',
            },
            action=change,
        )

        # Check mail
        self.assertEqual(len(mail.outbox), 0)

        # Trigger notification
        notify()
        self.validate_notifications(1, '[Weblate] Digest: {}'.format(subj))

    def test_digest_weekly(self):
        self.test_digest(FREQ_WEEKLY, notify_weekly)

    def test_digest_monthly(self):
        self.test_digest(FREQ_MONTHLY, notify_monthly)

    def test_diget_new_lang(self):
        self.test_digest(
            change=Change.ACTION_REQUESTED_LANGUAGE,
            subj='New language'
        )


class SubscriptionTest(ViewTestCase):
    notification = MergeFailureNotification

    def get_users(self, frequency):
        change = Change.objects.create(
            action=Change.ACTION_FAILED_MERGE,
            component=self.component
        )
        notification = self.notification(None)
        return list(notification.get_users(frequency, change))

    def test_scopes(self):
        self.user.profile.watched.add(self.project)
        # Not subscriptions
        self.user.subscription_set.all().delete()
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 0)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 0)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 0)
        # Default subscription
        self.user.subscription_set.create(
            scope=SCOPE_DEFAULT,
            notification=self.notification.get_name(),
            frequency=FREQ_MONTHLY
        )
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 0)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 0)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 1)
        # Admin subscription
        self.user.subscription_set.create(
            scope=SCOPE_ADMIN,
            notification=self.notification.get_name(),
            frequency=FREQ_WEEKLY
        )
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 0)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 0)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 1)

        self.component.project.add_user(self.user, '@Administration')
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 0)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 1)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 0)
        # Project subscription
        self.user.subscription_set.create(
            scope=SCOPE_PROJECT,
            project=self.project,
            notification=self.notification.get_name(),
            frequency=FREQ_DAILY
        )
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 1)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 0)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 0)
        # Component subscription
        subscription = self.user.subscription_set.create(
            scope=SCOPE_COMPONENT,
            project=self.project,
            notification=self.notification.get_name(),
            frequency=FREQ_INSTANT
        )
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 1)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 0)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 0)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 0)
        # Disabled notification for component
        subscription.frequency = FREQ_NONE
        subscription.save()
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        self.assertEqual(len(self.get_users(FREQ_DAILY)), 0)
        self.assertEqual(len(self.get_users(FREQ_WEEKLY)), 0)
        self.assertEqual(len(self.get_users(FREQ_MONTHLY)), 0)

    def test_skip(self):
        self.user.profile.watched.add(self.project)
        # Not subscriptions
        self.user.subscription_set.all().delete()
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)
        # Default subscription
        self.user.subscription_set.create(
            scope=SCOPE_DEFAULT,
            notification=self.notification.get_name(),
            frequency=FREQ_INSTANT
        )
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 1)
        # Subscribe to parent event
        self.user.subscription_set.create(
            scope=SCOPE_DEFAULT,
            notification='NewAlertNotificaton',
            frequency=FREQ_INSTANT
        )
        self.assertEqual(len(self.get_users(FREQ_INSTANT)), 0)


class SendMailsTest(SimpleTestCase):
    @override_settings(
        EMAIL_HOST='nonexisting.weblate.org',
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'
    )
    def test_error_handling(self):
        send_mails([{}])
        self.assertEqual(len(mail.outbox), 0)
