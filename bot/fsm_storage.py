"""Shared FSM storage singleton.

Imported by main.py (to hand to the Dispatcher) and by admin.py (to open
an FSMContext for the *customer's* chat from inside an admin-triggered
handler, e.g. to ask them for a review after delivery) — both need the
exact same storage instance for that to work.
"""

from aiogram.fsm.storage.memory import MemoryStorage

storage = MemoryStorage()
