from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choosing_product = State()
    choosing_cart = State()
    entering_custom_amount = State()
    entering_player_id = State()
    confirming = State()
    awaiting_payment_proof = State()
    awaiting_review = State()
