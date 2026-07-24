# Diamond Bot — боти фурӯши top-up-и Free Fire

Боти Telegram барои фурӯши алмази Free Fire: мизоҷ бастаро интихоб мекунад, ID-и
бозингарро медиҳад, пардохт мекунад, ва пас аз тасдиқ алмаз ба ҳисобаш мерасад.

## Чӣ тавр воқеан кор мекунад (муҳим)

Ягон бот "алмази ройгон тавлид" намекунад. Ин бот як **дилолӣ/reseller** аст:
шумо алмазро арзонтар мехаред (аз Garena/Codashop/UniPin/дилоли дигар) ва бо
нархи баландтар мефурӯшед — фарқият фоидаи шумост.

## ⚠️ Пойгоҳи додаҳо (Render-ро бе ин ҳаргиз накунед)

Агар `DATABASE_URL` холӣ бошад, бот ба як файли SQLite маҳаллӣ такя мекунад.
Дар Render-и **ройгон** диски доимӣ вуҷуд надорад — бинобар ин ҳар деплойи нав
контейнерро аз сифр месозад ва файли SQLite **тамоман тоза мешавад**: ҳамаи
корбарон, фармоишҳо, балансҳои реферал ва нархҳое, ки бо `/setprice`/
`/mapproduct`/`/addproduct` танзим кардаед — гум мешаванд.

Барои ҳалли доимӣ (ройгон): пойгоҳи додаҳоро ба [Supabase](https://supabase.com)
(Postgres-и ройгон) кӯчонед:

1. Дар supabase.com ҳисоб созед, лоиҳаи нав (`New project`) созед.
2. **Project Settings → Database → Connection string → URI**-ро кушоед.
   Курси "Session pooler" ё пайвасти мустақимро гиред (на "Transaction
   pooler" — ин бот раванди дарозмуддат аст, на serverless).
3. Дар аввали он сатр `postgresql://`-ро ба `postgresql+asyncpg://`
   иваз кунед.
4. Дар Render → Environment, тағйирёбандаи `DATABASE_URL`-ро бо ин сатр
   (бо парол) илова кунед, ва деплой кунед.

Пас аз ин, бот худкор ҳамаи ҷадвалҳоро дар Supabase месозад (ва маҳсулоти
пешфарзро мебанад), ва додаҳо дигар бо ҳеҷ деплой гум намешаванд.

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
2. **Расонидани худкори алмаз/Stars** — ин ҳоло **сохта шудааст** барои
   [FazerCards](https://reseller.fazercards.com) (`bot/services/fazercards.py`
   + `bot/services/delivery.py`, синфи `FazerCardsDeliveryProvider`). Барои
   фаъол кардан:
   1. Дар Render `FAZERCARDS_API_KEY` ва `DELIVERY_PROVIDER=fazercards`
      гузоред.
   2. Дар бот (ҳамчун админ) `/fzr_categories Free Fire` занед — category_id-и
      дурустро ёбед.
   3. `/fzr_offers <category_id>` — рӯйхати offer_id-ҳоро бо нарх бинед.
   4. Барои ҳар маҳсулот: `/mapproduct <product_id> <category_id> <offer_id>`.
      Ин фармон худаш бонуси офери FazerCards-ро (масалан "110_diamonds" барои
      бастаи 100-адад = +10 бонус) муайян карда, дар маҳсулот сабт мекунад, то
      мизоҷ ҳамон бонусеро бинад, ки дар сайти таъминкунанда нишон дода мешавад.
      Агар нодуруст муайян шуд — бо дасти худ ислоҳ кунед: `/setbonus <product_id> <бонус>`.
   5. Ҳамин тавр — фармоиши он маҳсулот акнун худкор тавассути FazerCards
      иҷро мешавад (агар FazerCards field-и ID-и бозингарро худкор муайян
      карда натавонад, ба таври бехатар ба усули дастӣ бармегардад).
   6. `/fzr_validate_id` нишон медиҳад кадом category-ҳо санҷиши "номи
      аккаунт тавассути ID" (мисли Dilovar Bot) доранд — агар category-и
      шумо (масалан `free_fire_cis`) дар ин рӯйхат набошад, ин маҳдудияти
      худи FazerCards API аст, на хатои бот, ва барои он category ном
      нишон дода намешавад.

   Барои дилоли дигар (на FazerCards) — синфи умумии `AutoDeliveryProvider`-ро
   бо `deliver()`-и воқеии онҳо пур карда, `DELIVERY_PROVIDER=auto` гузоред.

То FazerCards-ро танзим накунед, бот дар усули **"manual"** кор мекунад:
пардохт бо расиди дастӣ тасдиқ мешавад, алмаз бо дасти админ фиристода шуда
бо як тугма қайд мешавад. Ин бехатар аст ва барои оғози тиҷорат кофӣ.

## Насб (санҷиши маҳаллӣ, дар компютери худ)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env-ро пур кунед: BOT_TOKEN аз @BotFather, ADMIN_CHAT_ID, ADMIN_USER_IDS
# PUBLIC_URL-ро холӣ гузоред — бот дар усули polling кор мекунад
python main.py
```

## Фаъол доштани бот 24 соат дар Render

Бот дар ду усул кор карда метавонад:

- **polling** (`PUBLIC_URL` холӣ) — барои санҷиши маҳаллӣ дар компютери худ.
- **webhook** (`PUBLIC_URL` пур) — барои Render ва дигар хостҳое, ки хидматро
  доим фаъол нигоҳ медоранд ва URL-и оммавӣ медиҳанд.

Қадамҳо дар Render (Web Service, на Background Worker, зеро webhook ба порт
гӯш мекунад):

1. Дар Render → Settings-и хидмати худ:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
2. Дар Render → Environment, ин тағйирёбандаҳоро илова кунед:
   - `BOT_TOKEN` — токени бот аз @BotFather
   - `ADMIN_CHAT_ID` — ID-и гурӯҳи/чати админ (барои огоҳиномаҳо)
   - `ADMIN_USER_IDS` — ID-и телеграмии шумо (админ), бо вергул ҷудо
   - `PUBLIC_URL` — маҳз ҳамон URL-и хидмати шумо, масалан
     `https://diamond-bot-qakk.onrender.com` (бе `/` дар охир)
   - `TELEGRAM_WEBHOOK_SECRET` — як сатри тасодуфии дароз (масалан аз
     `openssl rand -hex 32`), барои амният
   - `PORT`-ро **насозед** — Render онро худаш медиҳад
3. Deploy кунед. Ҳангоми оғоз бот худаш ба Telegram мегӯяд "ба ин URL
   навсозиҳоро фиристед" (`set_webhook`).
4. Санҷиш: `https://diamond-bot-qakk.onrender.com/` бояд "OK" нишон диҳад.
   Баъд дар Telegram ба бот `/start` фиристед.

### Огоҳии муҳим — нигаҳдории маълумот

Дар нақшаи ройгони Render диски маҳаллӣ доим аст, аммо баъзан ҳангоми
редеплой/рестарт метавонад тоза шавад. Файли SQLite (`diamond_bot.db`) дар
он ҷо нигоҳ дошта мешавад — яъне фармоишу маҳсулоти шумо метавонанд гум
шаванд. Барои тиҷорати ҷиддӣ дар оянда беҳтараш ба базаи доимӣ (масалан
Render PostgreSQL, нақшаи пулакӣ) гузаред. Барои оғози кор ва санҷиш ин
кофист.

## Илова кардани маҳсулот

Дар боти Telegram (ҳамчун админ):

```
/addproduct Starter 100 10 8
/addstars Basic 100 15 12
```

(ном, миқдор — алмаз ё Stars, нархи фурӯш бо сомонӣ, нархи харид бо сомонӣ)

`/addproduct` бастаи алмази Free Fire месозад, `/addstars` бастаи Telegram
Stars. `/products` ҳарду навъро нишон медиҳад, `/delproduct <ID>` хомӯш
мекунад.

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
