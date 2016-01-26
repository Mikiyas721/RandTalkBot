# RandTalkBot Bot matching you with a random person on Telegram.
# Copyright (C) 2016 quasiyoke
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
import asynctest
import datetime
from asynctest.mock import call,create_autospec, patch, Mock, CoroutineMock
from peewee import *
from playhouse.test_utils import test_database
from randtalkbot import stranger
from randtalkbot.stranger import Stranger, MissingPartnerError, StrangerError
from randtalkbot.stranger_sender import StrangerSenderError
from randtalkbot.stranger_sender_service import StrangerSenderService
from telepot import TelegramError

database = SqliteDatabase(':memory:')
stranger.database_proxy.initialize(database)

class TestStranger(asynctest.TestCase):
    def setUp(self):
        database.create_tables([Stranger])
        self.stranger = Stranger.create(
            invitation='foo',
            telegram_id=31416,
            )
        self.stranger2 = Stranger.create(
            invitation='bar',
            telegram_id=27183,
            )
        self.stranger3 = Stranger.create(
            invitation='baz',
            telegram_id=23571,
            )

    def tearDown(self):
        database.drop_tables([Stranger])

    @asynctest.ignore_loop
    def test_init(self):
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)

    @patch('randtalkbot.stranger.INVITATION_LENGTH', 5)
    @asynctest.ignore_loop
    def test_get_invitation(self):
        invitation = Stranger.get_invitation()
        self.assertIsInstance(invitation, str)
        self.assertEqual(len(invitation), 5)

    def test_add_bonus__ok(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.bonus_count = 1000
        self.stranger.save = Mock()
        yield from self.stranger.add_bonus()
        self.stranger.save.assert_called_once_with()
        self.assertEqual(self.stranger.bonus_count, 1001)
        sender.send_notification.assert_called_once_with(
            'You\'ve received one bonus for inviting a person to the bot. '
                'Bonuses will help you to find partners quickly. Total bonus count: {0}. '
                'Congratulations!',
            1001,
            )

    @patch('randtalkbot.stranger.LOGGER', Mock())
    @asyncio.coroutine
    def test_add_bonus__telegram_error(self):
        from randtalkbot.stranger import LOGGER
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.bonus_count = 1000
        self.stranger.save = Mock()
        error = TelegramError('foo_description', 123)
        sender.send_notification.side_effect = error
        yield from self.stranger.add_bonus()
        self.stranger.save.assert_called_once_with()
        self.assertEqual(self.stranger.bonus_count, 1001)
        LOGGER.warning.assert_called_once_with('Add bonus. Can\'t notify stranger %d: %s', 1, error)

    @patch('randtalkbot.stranger.asyncio')
    @asyncio.coroutine
    def test_advertise__people_are_searching(self, asyncio_mock):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.get_start_args = Mock(return_value='foo_start_args')
        self.stranger.looking_for_partner_from = datetime.datetime.utcnow()
        self.stranger.save()
        self.stranger2.looking_for_partner_from = datetime.datetime.utcnow()
        self.stranger2.save()
        yield from self.stranger._advertise()
        asyncio_mock.sleep.assert_called_once_with(30)
        self.assertEqual(
            sender.send_notification.call_args_list,
            [
                call(
                    'You\'re still searching for partner among {0} people. You can talk with some of them '
                        'right now if you remove partner\'s sex restrictions or extend the list '
                        'of languages you know using /setup command.\nMore people -- more fun! '
                        'Spread Rand Talk between your friends. '
                        'The more people will use your link -- the faster partner\'s search will be. '
                        'Share the following message in your chats:',
                    2,
                    ),
                call(
                    'Do you want to talk with somebody, practice in foreign languages or you just want '
                        'to have some fun? Rand Talk will help you! It\'s a bot matching you with '
                        'a random stranger of desired sex speaking on your language. {0}',
                    'telegram.me/RandTalkBot?start=foo_start_args',
                    ),
                ],
            )

    @patch('randtalkbot.stranger.asyncio')
    @asyncio.coroutine
    def test_advertise__people_are_not_searching(self, asyncio_mock):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.looking_for_partner_from = datetime.datetime.utcnow()
        self.stranger.save()
        yield from self.stranger._advertise()
        sender.send_notification.assert_not_called()

    @patch('randtalkbot.stranger.asyncio')
    @asyncio.coroutine
    def test_advertise_later(self, asyncio_mock):
        self.stranger._advertise = Mock(return_value='foo')
        self.stranger.advertise_later()
        asyncio_mock.get_event_loop.return_value.create_task \
            .assert_called_once_with('foo')

    def test_end_chatting__not_chatting_or_looking_for_partner(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        yield from self.stranger.end_chatting()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)
        sender.send.assert_not_called()
        sender.send_notification.assert_not_called()

    def test_end_chatting__chatting_stranger(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger2.partner = self.stranger
        self.stranger.partner = self.stranger2
        self.stranger.save()
        self.stranger2.kick = CoroutineMock()
        yield from self.stranger.end_chatting()
        sender.send_notification.assert_called_once_with(
            'Chat was finished. Feel free to /begin a new one.',
            )
        sender.send.assert_not_called()
        self.stranger2.kick.assert_called_once_with()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)

    @patch('randtalkbot.stranger.LOGGER', Mock())
    @asyncio.coroutine
    def test_end_chatting__chatting_stranger_has_blocked_the_bot(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger2.partner = self.stranger
        self.stranger.partner = self.stranger2
        self.stranger.save()
        self.stranger2.kick = CoroutineMock()
        sender.send_notification.side_effect = TelegramError('foo_description', 123)
        yield from self.stranger.end_chatting()
        self.stranger2.kick.assert_called_once_with()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)

    def test_end_chatting__buggy_stranger(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger2.partner = None
        self.stranger.partner = self.stranger2
        self.stranger.save()
        self.stranger2.kick = CoroutineMock()
        yield from self.stranger.end_chatting()
        sender.send_notification.assert_called_once_with(
            'Chat was finished. Feel free to /begin a new one.',
            )
        sender.send.assert_not_called()
        self.stranger2.kick.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)

    def test_end_chatting__looking_for_partner(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.looking_for_partner_from = datetime.datetime(1970, 1, 1)
        self.stranger.save()
        yield from self.stranger.end_chatting()
        sender.send_notification.assert_called_once_with(
            'Looking for partner was stopped.',
            )
        sender.send.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)

    @patch('randtalkbot.stranger.LOGGER', Mock())
    @asyncio.coroutine
    def test_end_chatting__stranger_looking_for_partner_has_blocked_the_bot(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.looking_for_partner_from = datetime.datetime(1970, 1, 1)
        self.stranger.save = Mock()
        sender.send_notification.side_effect = TelegramError('foo_description', 123)
        yield from self.stranger.end_chatting()
        sender.send_notification.assert_called_once_with(
            'Looking for partner was stopped.',
            )
        sender.send.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)
        self.stranger.save.assert_called_once_with()

    @asynctest.ignore_loop
    def test_get__common_languages__preserves_languages_order(self):
        self.stranger.languages = '["foo", "bar", "baz", "boo", "zen"]'
        self.stranger2.languages = '["zen", "baz", "zig", "foo", "zam", "baz"]'
        self.assertEqual(self.stranger.get_common_languages(self.stranger2), ["foo", "baz", "zen"])
        self.stranger.languages = '["zen", "bar", "baz", "foo", "boo"]'
        self.stranger2.languages = '["zen", "baz", "zig", "foo", "zam", "baz"]'
        self.assertEqual(self.stranger.get_common_languages(self.stranger2), ["zen", "baz", "foo"])

    @asynctest.ignore_loop
    def test_get_languages__has_languages(self):
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.assertEqual(self.stranger.get_languages(), ["foo", "bar", "baz"])

    @asynctest.ignore_loop
    def test_get_languages__no_languages(self):
        self.stranger.languages = None
        self.assertEqual(self.stranger.get_languages(), [])

    @asynctest.ignore_loop
    def test_get_languages__corrupted_json(self):
        self.stranger.languages = '["foo'
        self.assertEqual(self.stranger.get_languages(), ['en'])

    @asynctest.ignore_loop
    @patch('randtalkbot.stranger.StrangerSenderService', create_autospec(StrangerSenderService))
    def test_get_sender(self):
        from randtalkbot.stranger import StrangerSenderService
        StrangerSenderService.get_instance.return_value.get_or_create_stranger_sender \
            .return_value = 'foo_sender'
        self.assertEqual(self.stranger.get_sender(), 'foo_sender')
        StrangerSenderService.get_instance.return_value.get_or_create_stranger_sender \
            .assert_called_once_with(self.stranger)

    @asynctest.ignore_loop
    def test_get_start_args(self):
        self.assertEqual(self.stranger.get_start_args(), 'eyJpIjoiZm9vIn0=')

    @asynctest.ignore_loop
    def test_is_novice__novice(self):
        self.stranger.languages = None
        self.stranger.sex = None
        self.stranger.partner_sex = None
        self.assertTrue(self.stranger.is_novice())

    @asynctest.ignore_loop
    def test_is_novice__not_novice(self):
        self.stranger.languages = 'foo'
        self.stranger.sex = None
        self.stranger.partner_sex = None
        self.assertFalse(self.stranger.is_novice())

    @asynctest.ignore_loop
    def test_is_full__full(self):
        self.stranger.languages = 'foo'
        self.stranger.sex = 'foo'
        self.stranger.partner_sex = 'foo'
        self.assertTrue(self.stranger.is_full())

    @asynctest.ignore_loop
    def test_is_full__not_full(self):
        self.stranger.languages = 'foo'
        self.stranger.sex = 'foo'
        self.stranger.partner_sex = None
        self.assertFalse(self.stranger.is_full())

    def test_kick(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.partner = self.stranger2
        self.stranger.save()
        yield from self.stranger.kick()
        sender.send_notification.assert_called_once_with(
            'Your partner has left chat. Feel free to /begin a new conversation.',
            )
        sender.send.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)

    @patch('randtalkbot.stranger.LOGGER', Mock())
    @asyncio.coroutine
    def test_kick(self):
        from randtalkbot.stranger import LOGGER
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.partner = self.stranger2
        self.stranger.save()
        error =  TelegramError('foo_description', 123)
        sender.send_notification.side_effect =error
        yield from self.stranger.kick()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, None)
        LOGGER.warning.assert_called_once_with('Kick. Can\'t notify stranger %d: %s', stranger.id, error)

    @asynctest.ignore_loop
    def test_prevent_advertising__ok(self):
        deferred_advertising = Mock()
        self.stranger._deferred_advertising = deferred_advertising
        self.stranger.prevent_advertising()
        deferred_advertising.cancel.assert_called_once_with()
        self.assertEqual(self.stranger._deferred_advertising, None)

    @asynctest.ignore_loop
    def test_prevent_advertising__deferred_is_not_set(self):
        self.stranger.prevent_advertising()
        self.assertTrue(True)

    @asynctest.ignore_loop
    def test_prevent_advertising__deferred_is_none(self):
        self.stranger._deferred_advertising = None
        self.stranger.prevent_advertising()
        self.assertTrue(True)

    def test_send__ok(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        message = Mock()
        yield from self.stranger.send(message)
        sender.send.assert_called_once_with(message)
        sender.send_notification.assert_not_called()

    def test_send__sender_error(self):
        sender = CoroutineMock()
        sender.send.side_effect = StrangerSenderError()
        self.stranger.get_sender = Mock(return_value=sender)
        message = Mock()
        with self.assertRaises(StrangerError):
            yield from self.stranger.send(message)
        sender.send.assert_called_once_with(message)
        sender.send_notification.assert_not_called()

    @patch('randtalkbot.stranger.get_languages_names', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__all_languages_are_common(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=['Your partner is here.', 'Have a nice chat!'])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('Have a nice chat!'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. Have a nice chat!',
            )

    @patch('randtalkbot.stranger.get_languages_names', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__had_partner_already(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=['Here\'s another stranger.', 'Have a nice chat!'])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger.partner = self.stranger3
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Here\'s another stranger.'),
                call('Have a nice chat!'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Here\'s another stranger. Have a nice chat!',
            )

    @patch('randtalkbot.stranger.get_languages_names', Mock(return_value='Foo'))
    @asyncio.coroutine
    def test_notify_partner_found__knows_uncommon_languages_one_common(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=['Use {0} please.', 'Your partner is here.', ])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz", "boo"]'
        self.stranger2.languages = '["zet", "zen", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        get_languages_names.assert_called_once_with(['foo'])
        self.assertEqual(
            sender.update_translation.call_args_list,
            [
                call(self.stranger2),
                call(),
                ],
            )
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Use {0} please.'),
                call('Your partner is here.'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. Use Foo please.',
            )

    @patch('randtalkbot.stranger.get_languages_names', Mock(return_value='Foo, Bar'))
    @asyncio.coroutine
    def test_notify_partner_found__knows_uncommon_languages_several_common(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=['You can use the following languages: {0}.', 'Your partner is here.', ])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz", "boo"]'
        self.stranger2.languages = '["zet", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        get_languages_names.assert_called_once_with(['foo', 'bar'])
        self.assertEqual(
            sender.update_translation.call_args_list,
            [
                call(self.stranger2),
                call(),
                ],
            )
        self.assertEqual(
            sender._.call_args_list,
            [
                call('You can use the following languages: {0}.'),
                call('Your partner is here.'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. You can use the following languages: Foo, Bar.',
            )

    @patch('randtalkbot.stranger.get_languages_names', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__was_bonus_used(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=['Your partner is here.', 'You\'ve used one bonus. {0} bonus(es) left.'])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger.bonus_count = 1001
        self.stranger.looking_for_partner_from = datetime.datetime.utcnow()
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('You\'ve used one bonus. {0} bonus(es) left.'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. You\'ve used one bonus. 1000 bonus(es) left.',
            )

    @patch('randtalkbot.stranger.get_languages_names', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__was_bonus_used_no_bonuses_left(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=['Your partner is here.', 'You\'ve used your last bonus.'])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger.bonus_count = 1
        self.stranger.looking_for_partner_from = datetime.datetime.utcnow()
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('You\'ve used your last bonus.'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. You\'ve used your last bonus.',
            )

    @patch('randtalkbot.stranger.datetime', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__waiting_several_minutes(self):
        sender = CoroutineMock()
        sender._ = Mock(side_effect=[
            'Your partner is here.',
            'Your partner\'s been looking for you for {0} min. Say him \"Hello\" -- '
                'if he doesn\'t respond to you, launch search again by /begin command.',
            ])
        self.stranger.get_sender = Mock(return_value=sender)
        from randtalkbot.stranger import datetime as datetime_mock
        datetime_mock.datetime.utcnow.return_value = datetime.datetime(1970, 1, 1, 10, 11)
        self.stranger2.looking_for_partner_from = datetime.datetime(1970, 1, 1, 10, 0)
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('Your partner\'s been looking for you for {0} min. Say him \"Hello\" -- '
                    'if he doesn\'t respond to you, launch search again by /begin command.'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. Your partner\'s been looking for you for 11 min. '
                'Say him "Hello" -- if he doesn\'t respond to you, launch search again by /begin command.',
            )

    @patch('randtalkbot.stranger.datetime', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__waiting_several_hours(self):
        sender = CoroutineMock()
        sender._ = Mock(side_effect=[
            'Your partner is here.',
            'Your partner\'s been looking for you for {0} hr. Say him \"Hello\" -- '
                'if he doesn\'t respond to you, launch search again by /begin command.',
            ])
        self.stranger.get_sender = Mock(return_value=sender)
        from randtalkbot.stranger import datetime as datetime_mock
        datetime_mock.datetime.utcnow.return_value = datetime.datetime(1970, 1, 1, 11, 0)
        self.stranger2.looking_for_partner_from = datetime.datetime(1970, 1, 1, 10, 0)
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('Your partner\'s been looking for you for {0} hr. Say him \"Hello\" -- '
                    'if he doesn\'t respond to you, launch search again by /begin command.'),
                ],
            )
        sender.send_notification.assert_called_once_with(
            'Your partner is here. Your partner\'s been looking for you for 1 hr. '
                'Say him "Hello" -- if he doesn\'t respond to you, launch search again by /begin command.',
            )

    @patch('randtalkbot.stranger.datetime', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__partner_doesnt_wait(self):
        sender = CoroutineMock()
        sender._ = Mock(side_effect=[
            'Your partner is here.',
            'Have a nice chat!',
            ])
        self.stranger.get_sender = Mock(return_value=sender)
        from randtalkbot.stranger import datetime as datetime_mock
        datetime_mock.datetime.utcnow.return_value = datetime.datetime(1970, 1, 1, 11, 0)
        self.stranger.looking_for_partner_from = datetime.datetime(1970, 1, 1, 10, 0)
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('Have a nice chat!'),
                ],
            )
        sender.send_notification.assert_called_once_with('Your partner is here. Have a nice chat!')

    @patch('randtalkbot.stranger.datetime', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__waiting_only_a_little_bit(self):
        sender = CoroutineMock()
        sender._ = Mock(side_effect=[
            'Your partner is here.',
            'Have a nice chat!',
            ])
        self.stranger.get_sender = Mock(return_value=sender)
        from randtalkbot.stranger import datetime as datetime_mock
        datetime_mock.datetime.utcnow.return_value = datetime.datetime(1970, 1, 1, 10, 4)
        self.stranger2.looking_for_partner_from = datetime.datetime(1970, 1, 1, 10, 0)
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger2.languages = '["baz", "bar", "foo"]'
        yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('Have a nice chat!'),
                ],
            )
        sender.send_notification.assert_called_once_with('Your partner is here. Have a nice chat!')

    @patch('randtalkbot.stranger.get_languages_names', Mock())
    @asyncio.coroutine
    def test_notify_partner_found__telegram_error(self):
        from randtalkbot.stranger import get_languages_names
        sender = CoroutineMock()
        sender.send_notification.side_effect = TelegramError('foo_description', 123)
        sender.update_translation = Mock()
        sender._ = Mock(side_effect=[
            'Your partner is here.',
            'Have a nice chat!',
            ])
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger2.languages = '["baz", "bar", "foo"]'
        with self.assertRaises(StrangerError):
            yield from self.stranger.notify_partner_found(self.stranger2)
        self.assertEqual(
            sender._.call_args_list,
            [
                call('Your partner is here.'),
                call('Have a nice chat!'),
                ],
            )
        sender.send_notification.assert_called_once_with('Your partner is here. Have a nice chat!')

    def test_send_to_partner__chatting_stranger(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.partner = self.stranger2
        self.stranger2.send = CoroutineMock()
        self.stranger.save()
        message = Mock()
        yield from self.stranger.send_to_partner(message)
        self.stranger2.send.assert_called_once_with(message)
        sender.send_notification.assert_not_called()
        sender.send.assert_not_called()

    def test_send_to_partner__not_chatting_stranger(self):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        with self.assertRaises(MissingPartnerError):
            yield from self.stranger.send_to_partner(Mock())
        sender.send_notification.assert_not_called()
        sender.send.assert_not_called()

    @asynctest.ignore_loop
    def test_set_languages__ok(self):
        # 6 languages.
        self.stranger.set_languages(['ru', 'en', 'it', 'fr', 'de', 'pt', ])
        self.assertEqual(self.stranger.languages, '["ru", "en", "it", "fr", "de", "pt"]')

    @asynctest.ignore_loop
    def test_set_languages__same(self):
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.stranger.set_languages(['same'])
        self.assertEqual(self.stranger.languages, '["foo", "bar", "baz"]')

    @asynctest.ignore_loop
    def test_set_languages__empty(self):
        from randtalkbot.stranger import EmptyLanguagesError
        with self.assertRaises(EmptyLanguagesError):
            self.stranger.set_languages([])

    @asynctest.ignore_loop
    def test_set_languages__same_empty(self):
        from randtalkbot.stranger import EmptyLanguagesError
        self.stranger.languages = None
        with self.assertRaises(EmptyLanguagesError):
            self.stranger.set_languages(['same'])

    @asynctest.ignore_loop
    def test_set_languages__too_much(self):
        from randtalkbot.stranger import StrangerError
        self.stranger.languages = None
        with self.assertRaises(StrangerError):
            # 7 languages.
            self.stranger.set_languages(['ru', 'en', 'it', 'fr', 'de', 'pt', 'po'])

    @patch('randtalkbot.stranger.datetime')
    @asyncio.coroutine
    def test_set_looking_for_partner__chatting_stranger(self, datetime_mock):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.partner = self.stranger2
        self.stranger2.kick = CoroutineMock()
        self.stranger.save()
        datetime_mock.datetime.utcnow.return_value = datetime.datetime(1980, 1, 1)
        yield from self.stranger.set_looking_for_partner()
        self.stranger2.kick.assert_called_once_with()
        sender.send_notification.assert_called_once_with(
            'Looking for a stranger for you.',
            )
        sender.send.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, datetime.datetime(1980, 1, 1))

    @patch('randtalkbot.stranger.datetime')
    @asyncio.coroutine
    def test_set_looking_for_partner__looking_for_partner_already(self, datetime_mock):
        sender = CoroutineMock()
        self.stranger.get_sender = Mock(return_value=sender)
        self.stranger.partner = self.stranger2
        self.stranger2.kick = CoroutineMock()
        self.stranger.save()
        datetime_mock.datetime.utcnow.return_value = datetime.datetime(1980, 1, 1)
        yield from self.stranger.set_looking_for_partner()
        self.stranger2.kick.assert_called_once_with()
        sender.send_notification.assert_called_once_with(
            'Looking for a stranger for you.',
            )
        sender.send.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, None)
        self.assertEqual(stranger.looking_for_partner_from, datetime.datetime(1980, 1, 1))

    def test_set_partner__chatting_stranger(self):
        self.stranger2.partner = self.stranger
        self.stranger2.kick = CoroutineMock()
        self.stranger.partner = self.stranger2
        self.stranger.save()
        yield from self.stranger.set_partner(self.stranger3)
        self.stranger2.kick.assert_called_once_with()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, self.stranger3)
        self.assertEqual(stranger.looking_for_partner_from, None)

    def test_set_partner__buggy_chatting_stranger(self):
        self.stranger.send_notification_about_another_partner = CoroutineMock()
        self.stranger2.kick = CoroutineMock()
        self.stranger.partner = self.stranger2
        self.stranger.save()
        yield from self.stranger.set_partner(self.stranger3)
        self.stranger2.kick.assert_not_called()
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, self.stranger3)
        self.assertEqual(stranger.looking_for_partner_from, None)

    def test_set_partner__not_chatting_stranger_was_used_bonus(self):
        self.stranger.looking_for_partner_from = datetime.datetime.utcnow()
        self.stranger.bonus_count = 1
        yield from self.stranger.set_partner(self.stranger3)
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, self.stranger3)
        self.assertEqual(stranger.bonus_count, 0)
        self.assertEqual(stranger.looking_for_partner_from, None)

    def test_set_partner__not_chatting_stranger_was_not_used_bonus(self):
        self.stranger.looking_for_partner_from = None
        self.stranger.bonus_count = 1
        yield from self.stranger.set_partner(self.stranger3)
        stranger = Stranger.get(Stranger.telegram_id == 31416)
        self.assertEqual(stranger.partner, self.stranger3)
        self.assertEqual(stranger.looking_for_partner_from, None)

    @asynctest.ignore_loop
    def test_set_sex__correct(self):
        self.stranger.set_sex('  mALe ')
        self.assertEqual(self.stranger.sex, 'male')

    @asynctest.ignore_loop
    def test_set_sex__translated(self):
        self.stranger.set_sex('  МУЖСКОЙ ')
        self.assertEqual(self.stranger.sex, 'male')

    @asynctest.ignore_loop
    def test_set_sex__additional(self):
        self.stranger.set_sex('  МАЛЬЧИК ')
        self.assertEqual(self.stranger.sex, 'male')

    @asynctest.ignore_loop
    def test_set_sex__incorrect(self):
        from randtalkbot.stranger import SexError
        self.stranger.sex = 'foo'
        with self.assertRaises(SexError):
            self.stranger.set_sex('not_a_sex')
        self.assertEqual(self.stranger.sex, 'foo')

    @asynctest.ignore_loop
    def test_set_partner_sex__correct(self):
        self.stranger.set_partner_sex('  mALe ')
        self.assertEqual(self.stranger.partner_sex, 'male')

    @asynctest.ignore_loop
    def test_set_partner_sex__additional(self):
        self.stranger.set_partner_sex('  МАЛЬЧИК ')
        self.assertEqual(self.stranger.partner_sex, 'male')

    @asynctest.ignore_loop
    def test_set_partner_sex__incorrect(self):
        from randtalkbot.stranger import SexError
        self.stranger.partner_sex = 'foo'
        with self.assertRaises(SexError):
            self.stranger.set_partner_sex('not_a_sex')
        self.assertEqual(self.stranger.partner_sex, 'foo')

    @asynctest.ignore_loop
    def test_speaks_on_language__novice(self):
        self.stranger.languages = None
        self.assertFalse(self.stranger.speaks_on_language('foo'))

    @asynctest.ignore_loop
    def test_speaks_on_language__speaks(self):
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.assertTrue(self.stranger.speaks_on_language('bar'))

    @asynctest.ignore_loop
    def test_speaks_on_language__not_speaks(self):
        self.stranger.languages = '["foo", "bar", "baz"]'
        self.assertFalse(self.stranger.speaks_on_language('boo'))
