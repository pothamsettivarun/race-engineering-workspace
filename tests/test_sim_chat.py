import unittest
from unittest.mock import patch
from sim_chat import SimChat


class SimChatTests(unittest.TestCase):
    def test_private_recipient_is_explicit_and_text_cannot_inject_new_commands(self):
        text=SimChat.private_text('064','Race Engineer\n/all injected$','LF tires\r\n#fuel 10$')
        self.assertTrue(text.startswith('/064 (Stint) Pit selections applied by Race Engineer'))
        self.assertNotIn('\n',text);self.assertNotIn('\r',text);self.assertNotIn('$',text)
        self.assertLessEqual(len(text),240)
        for recipient in ['','/all','64 other',None,'64\n/all']:
            with self.assertRaises(ValueError):SimChat.private_text(recipient,'Race Engineer','LF tires')

    def test_only_bounded_auto_fuel_macros_are_generated(self):
        chat=SimChat(None)
        with patch.object(chat,'_write',return_value=True) as send:
            chat.auto_fuel(True,1.2);send.assert_called_with('#autofuel 1.2')
            chat.auto_fuel(True,None);send.assert_called_with('#autofuel')
            chat.auto_fuel(False,None);send.assert_called_with('#-autofuel')
            for enabled,margin in [(1,1),(True,11),(True,'#fuel 100'),(True,float('nan'))]:
                with self.assertRaises(ValueError):chat.auto_fuel(enabled,margin)

    def test_zero_amount_uses_only_a_bounded_relative_fuel_macro(self):
        chat=SimChat(None)
        with patch.object(chat,'_write',return_value=True) as send:
            chat.zero_fuel_amount(2);send.assert_called_with('#fuel -2l')
            for amount in [0,-1,True,1001,'#fuel 100',float('nan')]:
                with self.assertRaises(ValueError):chat.zero_fuel_amount(amount)


if __name__=='__main__':unittest.main()
