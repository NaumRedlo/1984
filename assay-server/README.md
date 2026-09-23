# assay-server

Сервис, который считает pp и звёзды для бота 1984 **кодом самой osu!** — NuGet-пакетами
`ppy.osu.Game` и `ppy.osu.Game.Rulesets.*`. Своих формул в нём нет, поэтому, чтобы pp
совпадал с игрой после очередного изменения, достаточно поднять версию пакетов.

Устроено по той же схеме, что и в [osubot-telegram](https://github.com/Airkek/osubot-telegram):
отдельный сервис рядом с ботом. Код написан заново — у osubot-telegram лицензия GPL-3.0,
а пакеты ppy под MIT.

## Что умеет

| Запрос | Что делает |
|---|---|
| `GET /health` | статус и версия osu!, на которой считает сервис (без токена) |
| `POST /v1/beatmap` | звёзды, макс. комбо, скорость и все атрибуты сложности для набора модов |
| `POST /v1/score` | pp скора, pp «если FC» и «если SS», точность, разбивка (aim/speed/accuracy/flashlight/reading…) |
| `POST /v1/whatif` | pp для списка точностей (до 20) без промахов или с заданным числом промахов |

- **Моды с настройками**: `"mods": [{"acronym": "DT", "settings": {"speed_change": 1.2}}]`, либо просто `"HDDT"` или `["HD","DT"]`.
  Несовместимые и неизвестные моды отклоняются.
- **Статистика в формате osu! API v2**: `great`, `ok`, `meh`, `miss`, `large_tick_hit`, `large_tick_miss`,
  `slider_tail_hit`, `legacy_combo_increase` и т.д.; старые `count_300/100/50/miss` тоже понимаются.
  Чего lazer-скор не прислал (тики, хвосты), достраивается из карты.
- **Stable-скоры**: `legacy_total_score` (или `is_legacy: true`) — точность считается по классической формуле,
  оценка промахов по счёту работает как в osu!.
- **Карты по MD5**: `.osu` хранятся под своим хэшем. Если передан `checksum` (он есть в ответе osu! API
  у каждой карты), сервис берёт именно эту версию, а если osu! отдал другую — отвечает `409`, а не считает
  по старой. Без `checksum` карта перепроверяется раз в `ASSAY_UNVERIFIED_HOURS`.
- Режимы: osu!, taiko, catch, mania для звёзд и pp с готовой статистикой; «если FC/SS» и what-if — только osu!.

Пример:

```bash
curl -s localhost:5077/v1/score -H 'Authorization: Bearer …' -H 'content-type: application/json' -d '{
  "beatmap_id": 1494828, "checksum": "257099a7bc15792928a55c1a8510e934",
  "mods": [{"acronym": "HD"}, {"acronym": "DT", "settings": {"speed_change": 1.3}}],
  "statistics": {"great": 1680, "ok": 20, "miss": 4, "large_tick_miss": 1, "slider_tail_hit": 550},
  "max_combo": 1200 }'
```

## Проверено

Тесты (`dotnet test`) сверяют сервис с корпусом из репозитория Dossier:

- **400 скоров** (10 карт × NM, HD, HR, DT, HDDT, EZ и их classic-варианты × 10 видов игры) — pp совпадает
  с `osu-tools` точнее 0,1%, точность — до 6 знаков;
- **150 звёздных рейтингов** (10 карт × 15 наборов модов) — совпадают с osu! API точнее 0,001*,
  кроме **HDFL**: там сервис на ~1% ниже снимка из API (одинаково на версиях 2026.730.0 и 2026.916.0).
  Какое значение сейчас на osu!, покажет живая проверка ниже;
- «если SS» для скора с промахами равен pp идеальной игры из корпуса; what-if попадает в заданную точность.

## Запуск

Переменные окружения:

| Переменная | По умолчанию | Что это |
|---|---|---|
| `ASSAY_URLS` | `http://127.0.0.1:5077` | где слушать |
| `ASSAY_TOKEN` | пусто | если задан — все запросы, кроме `/health`, требуют `Authorization: Bearer <токен>` |
| `ASSAY_MAPS` | `./maps` рядом с программой | где хранить `.osu` |
| `ASSAY_UNVERIFIED_HOURS` | `24` | как долго доверять карте, скачанной без `checksum` |
| `ASSAY_CACHED_MAPS` | `512` | сколько посчитанных сочетаний «карта+моды» держать в памяти |
| `ASSAY_WORKERS` | число ядер | сколько расчётов идёт одновременно |
| `ASSAY_OSU_URL` | `https://osu.ppy.sh/` | откуда качать карты |

**Без Docker** (нужен .NET 10 runtime):

```bash
cd assay-server
dotnet publish src/Assay/Assay.csproj -c Release -r linux-x64 --self-contained false -o /opt/assay
/opt/assay/Assay --self-test
sudo cp deploy/assay.service /etc/systemd/system/ && sudo systemctl enable --now assay
```

**Docker:**

```bash
docker build -t assay assay-server
docker run -d --name assay -p 127.0.0.1:5077:5077 -v assay-maps:/var/lib/assay/maps -e ASSAY_TOKEN=… assay
```

Сборка (≈96 МБ) выкидывает `osu.Game.Resources.dll` — текстуры и звуки игры расчёту не нужны.
И `docker build`, и CI запускают `--self-test`, так что неработающий образ не соберётся.

**Боту** в `.env`: `ASSAY_URL=http://127.0.0.1:5077` и тот же `ASSAY_TOKEN` (`utils/osu/assay_service.py`).
Пока `ASSAY_URL` пуст, бот считает как раньше.

## Как держать pp актуальным

1. **Dependabot** (`.github/dependabot.yml`) каждый день проверяет NuGet и открывает один PR на все пакеты
   `ppy.osu.Game*` сразу. На PR запускаются тесты по корпусу: если osu! изменила формулу, тесты покажут,
   какие цифры и насколько сдвинулись. Проверил — смёрджил — пересобрал сервис.
2. **Живая проверка** (`tools/check_live.py`, задача `live` в `.github/workflows/assay.yml`, каждый понедельник)
   сравнивает звёзды и pp топ-скоров на картах корпуса с тем, что сейчас отдаёт osu! API. Нужны секреты
   репозитория `OSU_CLIENT_ID` и `OSU_CLIENT_SECRET` (OAuth-приложение на osu.ppy.sh); без них задача
   предупреждает и пропускается. Красная проверка при зелёных тестах значит, что osu! уже считает иначе,
   а пакеты ещё не вышли или не обновлены.
3. **В самом боте** каждый ранкнутый скор, для которого osu! прислал pp, сравнивается с посчитанным;
   расхождение больше 1% пишется в лог как `pp drift` с указанием, кто считал (`assay`, `engine` или rosu-pp-py).
