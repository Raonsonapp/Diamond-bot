# Diamond Bot — боти фурӯши top-up-и Free Fire

Боти Telegram барои фурӯши алмази Free Fire: мизоҷ бастаро интихоб мекунад, ID-и
бозингарро медиҳад, пардохт мекунад, ва пас аз тасдиқ алмаз ба ҳисобаш мерасад.

## Чӣ тавр воқеан кор мекунад (муҳим)

Ягон бот "алмази ройгон тавлид" намекунад. Ин бот як **дилолӣ/reseller** аст:
шумо алмазро арзонтар мехаред (аз Garena/Codashop/UniPin/дилоли дигар) ва бо
нархи баландтар мефурӯшед — фарқият фоидаи шумост.

## Чӣ ҳоло кор мекунад (out of the box)

- Феҳристи маҳсулот (бастаҳои алмаз) бо нарх ва арзиши харид (барои ҳисоби фоида)
- Ҷараёни пурраи фармоиш: интихоби баста → ID-и бозингар → тасдиқ
- Пардохт бо усули **"manual"**: мизоҷ бо карт мегузаронад, скриншот мефиристад,
  админ дар худи бот бо як тугма тасдиқ/рад мекунад
- Панели админ: `/addproduct`, `/products`, `/pending`, тугмаҳои тасдиқ/рад/"Delivered"
- Пас аз тасдиқи пардохт мизоҷ фавран огоҳ мешавад; пас аз "Delivered" низ

## Чӣ ҳанӯз кор намекунад ва бояд шумо пур кунед

Шумо гуфтед мехоҳед раванд **пурра худкор** бошад (ҳатто вақте офлайн ҳастед).
Барои ин ду интеграция бояд бо маълумоти воқеӣ пур карда шаванд — ман онҳоро
мадомест нагузоштам, зеро бе ҳисоби воқеӣ онҳо кор намекунанд ва хатари гирифтани
пул бе расонидани хизмат доранд:

1. **Пардохти онлайн (Alif/Paynet/Eskhata)** — `bot/services/payments.py`,
   синфи `AlifPayProvider`. Alif ҳуҷҷати оммавии API надорад — Shop ID, Secret
   Key ва имзои webhook-ро баъд аз бастани шартномаи мерчант аз Alif Business
   мегиред. Онҳоро пур карда, дар `.env` `PAYMENT_PROVIDER=alif` гузоред.
2. **Расонидани худкори алмаз** — `bot/services/delivery.py`, синфи
   `AutoDeliveryProvider`. Барои ин бояд аккаунти дилоли (reseller) real доред,
   ки API-и top-up дорад. Ҳамин ки чунин дилол ёфтед, `deliver()`-ро ба
   endpoint-и воқеии онҳо пайваст кунед ва `DELIVERY_PROVIDER=auto` гузоред.

То ҳамон вақт бот дар усули **"manual"** кор мекунад: пардохт бо расиди дастӣ
тасдиқ мешавад, алмаз бо дасти админ фиристода шуда бо як тугма қайд мешавад.
Ин бехатар аст ва барои оғози тиҷорат кофӣ — фақат бе шумо (офлайн) кор намекунад.

## Насб

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env-ро пур кунед: BOT_TOKEN аз @BotFather, ADMIN_CHAT_ID, ADMIN_USER_IDS
python main.py
```

## Илова кардани маҳсулот

Дар боти Telegram (ҳамчун админ):

```
/addproduct Starter 100 10 8
```

(ном, миқдори алмаз, нархи фурӯш бо сомонӣ, нархи харид бо сомонӣ)

## Сохти лоиҳа

```
bot/
  config.py           # хониши .env
  states.py           # FSM-и ҷараёни фармоиш
  keyboards.py         # inline-тугмаҳо
  db/
    models.py          # User, Product, Order
    session.py         # SQLite engine/session
    repo.py             # CRUD
  services/
    payments.py        # PaymentProvider: manual (кор мекунад) + alif (скелет)
    delivery.py         # DeliveryProvider: manual (кор мекунад) + auto (скелет)
  handlers/
    customer.py        # /start, интихоби маҳсулот, фармоиш, пардохт
    admin.py             # /addproduct, /products, /pending, тасдиқ/рад/delivered
main.py                 # нуқтаи вуруд (polling)
```
