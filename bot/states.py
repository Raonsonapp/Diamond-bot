from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choosing_product = State()
    choosing_cart = State()
    entering_custom_amount = State()
    entering_player_id = State()
    choosing_region = State()
    confirming = State()
    awaiting_payment_proof = State()
    awaiting_admin_review = State()
    awaiting_review = State()


class WalletTopupFlow(StatesGroup):
    awaiting_proof = State()


class ApiAccessFlow(StatesGroup):
    setting_pin = State()
    checking_pin = State()
