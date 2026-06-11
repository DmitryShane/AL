export type DocumentationLanguage = "en" | "ru";

type LocalizedText = Record<DocumentationLanguage, string>;

export type DocumentationReference = {
  label: LocalizedText;
  href: string;
};

export type DocumentationRule = {
  title: LocalizedText;
  summary: LocalizedText;
  details: LocalizedText[];
  references: DocumentationReference[];
};

export type DocumentationCategory = {
  id: string;
  title: LocalizedText;
  kicker: LocalizedText;
  intro: LocalizedText;
  rules: DocumentationRule[];
};

export const DOCUMENTATION_LANGUAGES: { key: DocumentationLanguage; label: string }[] = [
  { key: "en", label: "English" },
  { key: "ru", label: "Russian" }
];

export const DOCUMENTATION_CATEGORIES: DocumentationCategory[] = [
  {
    id: "overview",
    title: { en: "Overview", ru: "Обзор" },
    kicker: { en: "How to read AL", ru: "Как читать AL" },
    intro: {
      en: "Activity Logger combines work chat signals, plugin activity, meetings, device activity, calendar overrides, and settings into one operational dashboard.",
      ru: "Activity Logger объединяет сигналы рабочего чата, активность плагинов, встречи, устройства, календарные отметки и настройки в один операционный dashboard."
    },
    rules: [
      {
        title: { en: "Dashboard pages have different jobs", ru: "У страниц dashboard разные роли" },
        summary: {
          en: "Authors is the team overview, Activity is the per-author inspection page, Analytics compares trends, Calendar explains planned exceptions, Alerts is for issues, and Settings controls behavior.",
          ru: "Authors показывает команду в целом, Activity раскрывает конкретного автора, Analytics сравнивает тренды, Calendar объясняет плановые исключения, Alerts нужен для проблем, а Settings управляет поведением."
        },
        details: [
          {
            en: "Use Authors for a quick health check, then open Activity when a number or status needs explanation.",
            ru: "Используй Authors для быстрой проверки состояния, а Activity открывай, когда нужно объяснить конкретное число или статус."
          },
          {
            en: "Settings changes can affect how future activity is interpreted, while Calendar marks explain why a day should not look like a normal workday.",
            ru: "Изменения в Settings могут влиять на интерпретацию будущей активности, а Calendar объясняет, почему день не должен выглядеть как обычный рабочий день."
          }
        ],
        references: [
          { label: { en: "View Authors", ru: "Открыть Authors" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "View Activity", ru: "Открыть Activity" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "View Settings", ru: "Открыть Settings" }, href: "/settings?tab=general&docTarget=settings-general" }
        ]
      },
      {
        title: { en: "Sources are merged into one author story", ru: "Источники собираются в историю автора" },
        summary: {
          en: "Editor plugins, device reports, work chat events, Discord meetings, and calendar marks are shown together under the author profile.",
          ru: "Editor-плагины, device reports, события рабочего чата, Discord-встречи и календарные отметки показываются вместе в профиле автора."
        },
        details: [
          {
            en: "Author profiles and redirects decide which raw names belong to the same person.",
            ru: "Author Profiles и Redirects определяют, какие raw names относятся к одному человеку."
          },
          {
            en: "Device and publisher profiles are used when activity arrives from a device-only or external profile and must be linked back to a visible author.",
            ru: "Device и Publisher Profiles нужны, когда активность приходит от устройства или внешнего профиля и должна быть связана с видимым автором."
          }
        ],
        references: [
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" },
          { label: { en: "Author Redirects", ru: "Author Redirects" }, href: "/settings?tab=redirects&docTarget=settings-redirects" },
          { label: { en: "Device Profiles", ru: "Device Profiles" }, href: "/settings?tab=deviceProfiles&docTarget=settings-deviceProfiles" }
        ]
      },
      {
        title: { en: "Date context changes what the dashboard means", ru: "Контекст даты меняет смысл dashboard" },
        summary: {
          en: "Live views, selected dates, and historical snapshots should be read as different operating modes.",
          ru: "Live views, выбранные даты и historical snapshots нужно читать как разные режимы работы."
        },
        details: [
          {
            en: "Today is useful for current decisions. Historical dates are useful for review, but they should be checked with calendar marks and stored snapshots.",
            ru: "Today полезен для текущих решений. Historical dates полезны для review, но их нужно проверять вместе с calendar marks и stored snapshots."
          }
        ],
        references: [
          { label: { en: "Activity date controls", ru: "Date controls в Activity" }, href: "/activity?docTarget=activity-overview" },
          { label: { en: "Activity Snapshots", ru: "Activity Snapshots" }, href: "/settings?tab=snapshots&docTarget=settings-snapshots" }
        ]
      },
      {
        title: { en: "Investigations should move from summary to evidence", ru: "Расследование идет от summary к evidence" },
        summary: {
          en: "Start with a card or metric, then inspect the hourly chart, breakdown cards, and report rows before changing settings.",
          ru: "Начинай с карточки или метрики, затем проверяй hourly chart, breakdown cards и report rows до изменения настроек."
        },
        details: [
          {
            en: "This order helps separate real behavior from display context, calendar exceptions, identity mapping, and configuration mistakes.",
            ru: "Такой порядок помогает отделить реальное поведение от display context, calendar exceptions, identity mapping и ошибок конфигурации."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      }
    ]
  },
  {
    id: "authors-page-complete",
    title: { en: "Authors Page & Author Table", ru: "Authors page и таблица авторов" },
    kicker: { en: "Roster, sorting, and source freshness", ru: "Roster, sorting и свежесть source" },
    intro: {
      en: "The Authors page is the roster view: it helps find people quickly, compare their current state, and decide which author needs Activity-level investigation.",
      ru: "Authors page — это roster view: она помогает быстро найти людей, сравнить текущее состояние и понять, какого автора нужно проверять на Activity."
    },
    rules: [
      {
        title: { en: "Search filters the visible roster", ru: "Search фильтрует видимый roster" },
        summary: {
          en: "Search is for quickly narrowing the author list by visible identity and source context before opening a detailed Activity view.",
          ru: "Search нужен, чтобы быстро сузить список авторов по visible identity и source context перед переходом в Activity."
        },
        details: [
          { en: "If a person is missing from search, check Author Profiles, Redirects, Publisher Profiles, and Device Profiles before assuming activity is gone.", ru: "Если человек не находится через search, проверь Author Profiles, Redirects, Publisher Profiles и Device Profiles до вывода, что activity пропала." }
        ],
        references: [
          { label: { en: "Authors overview", ru: "Authors overview" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-author-profiles-editor" }
        ]
      },
      {
        title: { en: "Sorting is for triage, not final proof", ru: "Sorting нужен для triage, не для final proof" },
        summary: {
          en: "Sorting by status, productivity, report freshness, or time totals helps decide what to inspect first.",
          ru: "Sorting по status, productivity, report freshness или time totals помогает понять, что проверять первым."
        },
        details: [
          { en: "Use Activity metrics, the hourly chart, and report rows to confirm the reason behind any surprising sort result.", ru: "Используй Activity metrics, hourly chart и report rows, чтобы подтвердить причину любого неожиданного sort result." }
        ],
        references: [
          { label: { en: "Authors overview", ru: "Authors overview" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Activity metrics", ru: "Activity metrics" }, href: "/activity?docTarget=activity-metrics" }
        ]
      },
      {
        title: { en: "Author table columns match operational questions", ru: "Колонки author table отвечают operational questions" },
        summary: {
          en: "The table groups Day Time, Plugin time, Active Time, Idle Time, Meeting, Overtime, Break Time, Productivity, Status, Plugin, and Last Report in one scan-friendly row.",
          ru: "Таблица группирует Day Time, Plugin time, Active Time, Idle Time, Meeting, Overtime, Break Time, Productivity, Status, Plugin и Last Report в одной scan-friendly строке."
        },
        details: [
          { en: "Day Time follows work chat boundaries; Plugin time follows incoming source reports; productivity compares productive tracked time against the current context.", ru: "Day Time следует границам work chat; Plugin time следует incoming source reports; productivity сравнивает productive tracked time с текущим context." }
        ],
        references: [
          { label: { en: "Authors overview", ru: "Authors overview" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Metrics rules", ru: "Metrics rules" }, href: "/documentation#activity-metrics" }
        ]
      },
      {
        title: { en: "Status colors keep grey offline separate from reports stopped", ru: "Status colors отделяют grey offline от reports stopped" },
        summary: {
          en: "Grey offline is expected inactive context; red reports stopped means an open workday expected reports and the Plugin source became stale.",
          ru: "Grey offline — ожидаемый inactive context; red reports stopped означает, что open workday ожидал reports, а Plugin source стал stale."
        },
        details: [
          { en: "Do not treat explicit sign-off as a reporting failure just because the author is not currently online.", ru: "Не считай explicit sign-off reporting failure только потому, что author сейчас не online." }
        ],
        references: [
          { label: { en: "Status card rules", ru: "Status card rules" }, href: "/documentation#authors-status" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Plugin and Last Report explain freshness", ru: "Plugin и Last Report объясняют freshness" },
        summary: {
          en: "The Plugin column shows which source is currently explaining the author row, while Last Report shows how fresh that evidence is.",
          ru: "Колонка Plugin показывает, какой source сейчас объясняет строку автора, а Last Report показывает свежесть evidence."
        },
        details: [
          { en: "When Last Report is stale during an open day, inspect Plugin Reports and source filters before changing profile settings.", ru: "Когда Last Report stale во время open day, проверь Plugin Reports и source filters до изменения profile settings." }
        ],
        references: [
          { label: { en: "Authors overview", ru: "Authors overview" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Empty or narrowed results are a filter state", ru: "Empty или narrowed results — это filter state" },
        summary: {
          en: "If the Authors table looks too small, clear search and compare the roster with Settings identity tabs before treating it as missing data.",
          ru: "Если Authors table выглядит слишком маленькой, очисти search и сравни roster с identity tabs в Settings до вывода о missing data."
        },
        details: [
          { en: "The Authors page is a dashboard view; identity creation, redirects, and device ownership are managed in Settings.", ru: "Authors page — dashboard view; identity creation, redirects и device ownership управляются в Settings." }
        ],
        references: [
          { label: { en: "Authors overview", ru: "Authors overview" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Settings identity tabs", ru: "Settings identity tabs" }, href: "/documentation#settings-complete" }
        ]
      }
    ]
  },
  {
    id: "authors-status",
    title: { en: "Authors & Status Cards", ru: "Авторы и статус-карточки" },
    kicker: { en: "Presence, productivity, exceptions", ru: "Presence, productivity, исключения" },
    intro: {
      en: "Author cards are the fastest way to see who is working, who signed off, who has stopped reporting, and whose day has a calendar exception.",
      ru: "Карточки авторов быстрее всего показывают, кто работает, кто закрыл день, у кого перестали приходить отчеты и у кого есть календарное исключение."
    },
    rules: [
      {
        title: { en: "Online means the workday is open", ru: "Online означает открытый рабочий день" },
        summary: {
          en: "An online card means the author has an active workday and current activity can count toward the normal day unless another rule turns it into overtime, break, meeting, or idle.",
          ru: "Online-карточка означает, что у автора открыт рабочий день, и текущая активность может попадать в обычный день, если другое правило не превращает ее в overtime, break, meeting или idle."
        },
        details: [
          {
            en: "The Activity page shows the selected author’s detailed metrics, hourly chart, breakdowns, and report rows.",
            ru: "Activity показывает выбранного автора подробно: метрики, часовой график, breakdowns и строки reports."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Selected author summary", ru: "Сводка выбранного автора" }, href: "/activity?docTarget=activity-selected-author" }
        ]
      },
      {
        title: { en: "Grey offline is an expected inactive state", ru: "Серый offline — ожидаемое неактивное состояние" },
        summary: {
          en: "Grey offline is used before an author starts the current workday, after an explicit sign-off, for device-only inactivity, and for historical snapshots.",
          ru: "Серый offline используется до начала рабочего дня, после явного sign-off, для device-only inactive состояния и для исторических snapshot-ов."
        },
        details: [
          {
            en: "It is not a failure by itself. It usually means the system is not expecting fresh plugin reports for a currently open workday.",
            ru: "Сам по себе это не ошибка. Обычно это значит, что система не ожидает свежие plugin reports для открытого рабочего дня."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Calendar exceptions", ru: "Календарные исключения" }, href: "/calendar?docTarget=calendar-overview" }
        ]
      },
      {
        title: { en: "Red offline means reports stopped", ru: "Красный offline означает reports stopped" },
        summary: {
          en: "Red offline is reserved for a failure state: the author has started the workday, reports are expected, and plugin reports have stopped.",
          ru: "Красный offline зарезервирован для failure-состояния: автор начал рабочий день, отчеты ожидаются, но plugin reports перестали приходить."
        },
        details: [
          {
            en: "This is different from an explicit work chat offline. A signed-off author should not be shown as a red failure just because they ended the day.",
            ru: "Это не то же самое, что явный offline в рабочем чате. Автор, который закрыл день, не должен выглядеть как красная ошибка только из-за завершения дня."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Vacation changes the meaning of a day", ru: "Vacation меняет смысл дня" },
        summary: {
          en: "A vacation mark explains why a normal workday should not be expected and keeps the card from being judged like a regular working day.",
          ru: "Vacation mark объясняет, почему обычный рабочий день не ожидается, и не дает оценивать карточку как стандартный рабочий день."
        },
        details: [
          {
            en: "Overtime can still appear if activity is recorded on a marked day, but normal active expectations are different.",
            ru: "Overtime все еще может появиться, если активность записана в отмеченный день, но ожидания по обычному active времени другие."
          }
        ],
        references: [
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" },
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" }
        ]
      },
      {
        title: { en: "Author cards are a current-state summary", ru: "Карточки авторов — summary текущего состояния" },
        summary: {
          en: "A card compresses workday state, reporting health, activity totals, productivity, team identity, and calendar context into one place.",
          ru: "Карточка сжимает workday state, reporting health, activity totals, productivity, team identity и calendar context в одно место."
        },
        details: [
          {
            en: "Use the card to decide what to inspect next, not as the only source of truth for a disputed hour.",
            ru: "Используй карточку, чтобы понять, что проверять дальше, а не как единственный источник истины для спорного часа."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Selected author", ru: "Selected author" }, href: "/activity?docTarget=activity-selected-author" }
        ]
      },
      {
        title: { en: "Last report time explains stale states", ru: "Last report time объясняет stale states" },
        summary: {
          en: "When a card looks inactive or red, compare the visible status with the latest report time and source.",
          ru: "Когда карточка выглядит inactive или красной, сравнивай видимый статус с latest report time и source."
        },
        details: [
          {
            en: "A stopped source during an open workday is a reporting problem. No expected reports after sign-off is a normal inactive state.",
            ru: "Остановившийся source при открытом workday — reporting problem. Отсутствие ожидаемых reports после sign-off — нормальное inactive state."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Identity issues should be fixed in profiles, not cards", ru: "Identity-проблемы чинятся в profiles, не в карточках" },
        summary: {
          en: "If the wrong name, color, team, timezone, or source ownership appears on a card, fix the profile or redirect configuration.",
          ru: "Если на карточке неверные name, color, team, timezone или ownership source, исправляй profile или redirect configuration."
        },
        details: [
          {
            en: "Cards display the resolved author story; Settings controls how raw identities become that story.",
            ru: "Карточки показывают resolved author story; Settings управляет тем, как raw identities превращаются в эту историю."
          }
        ],
        references: [
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" },
          { label: { en: "Author Redirects", ru: "Author Redirects" }, href: "/settings?tab=redirects&docTarget=settings-redirects" }
        ]
      }
    ]
  },
  {
    id: "navigation-login-date",
    title: { en: "Navigation, Login & Date Context", ru: "Навигация, Login и контекст даты" },
    kicker: { en: "Shell, session, dates", ru: "Shell, session, dates" },
    intro: {
      en: "The dashboard shell controls authentication, sidebar navigation, session identity, role visibility, loading state, cached restoration, and the date context used by Authors and Activity.",
      ru: "Dashboard shell управляет authentication, sidebar navigation, session identity, role visibility, loading state, cached restoration и date context для Authors и Activity."
    },
    rules: [
      {
        title: { en: "Login and session gate the private dashboard", ru: "Login и session закрывают private dashboard" },
        summary: {
          en: "The login page is required before product pages are available; after sign-in the sidebar session card shows the current dashboard user and logout action.",
          ru: "Login page обязателен перед product pages; после входа sidebar session card показывает текущего dashboard user и logout action."
        },
        details: [
          { en: "If a user cannot see admin-only controls, verify their Site Users role and permissions before assuming the page is broken.", ru: "Если user не видит admin-only controls, проверь role и permissions в Site Users перед выводом, что page сломана." }
        ],
        references: [
          { label: { en: "Site Users", ru: "Site Users" }, href: "/settings?tab=users&docTarget=settings-site-users-panel" }
        ]
      },
      {
        title: { en: "Sidebar is the complete top-level map", ru: "Sidebar — полная top-level карта" },
        summary: {
          en: "Authors, Activity, Analytics, Calendar, Alerts, Settings, and Documentation are the current top-level product pages.",
          ru: "Authors, Activity, Analytics, Calendar, Alerts, Settings и Documentation — текущие top-level product pages."
        },
        details: [
          { en: "Use Documentation reference links for exact page sections and Settings tabs.", ru: "Используй Documentation reference links для exact page sections и Settings tabs." }
        ],
        references: [
          { label: { en: "Documentation overview", ru: "Documentation overview" }, href: "/documentation#overview" }
        ]
      },
      {
        title: { en: "Date presets decide whether the view is live or historical", ru: "Date presets решают live или historical view" },
        summary: {
          en: "Live selects today, Yesterday selects the previous day, and the selected day picker creates a custom historical one-day range.",
          ru: "Live выбирает today, Yesterday — previous day, а selected day picker создает custom historical one-day range."
        },
        details: [
          { en: "Future dates are rejected and reset to the latest valid day; historical dates should be checked with snapshots and calendar marks.", ru: "Future dates отклоняются и сбрасываются к последнему valid day; historical dates нужно проверять со snapshots и calendar marks." }
        ],
        references: [
          { label: { en: "Date picker", ru: "Date picker" }, href: "/activity?docTarget=date-range-picker" },
          { label: { en: "Snapshots", ru: "Snapshots" }, href: "/settings?tab=snapshots&docTarget=settings-snapshots-panel" }
        ]
      },
      {
        title: { en: "Cached and loading states are display context", ru: "Cached и loading states — display context" },
        summary: {
          en: "Cached rows can appear while fresh dashboard data loads; loading messages mean the displayed state may still be settling.",
          ru: "Cached rows могут показываться пока fresh dashboard data загружается; loading messages означают, что displayed state еще может меняться."
        },
        details: [
          { en: "Wait for fresh data before making operational decisions from stale-looking values.", ru: "Дождись fresh data перед operational decisions по stale-looking values." }
        ],
        references: [
          { label: { en: "Authors", ru: "Authors" }, href: "/authors?docTarget=authors-overview" },
          { label: { en: "Activity", ru: "Activity" }, href: "/activity?docTarget=activity-overview" }
        ]
      }
    ]
  },
  {
    id: "activity-ui-complete",
    title: { en: "Activity UI Complete", ru: "Activity UI полностью" },
    kicker: { en: "Every visible Activity workflow", ru: "Каждый visible Activity workflow" },
    intro: {
      en: "Activity combines author selection, date selection, refresh actions, selected-author tables, metrics, charts, breakdowns, reports, and special loading/empty states.",
      ru: "Activity объединяет author selection, date selection, refresh actions, selected-author tables, metrics, charts, breakdowns, reports и special loading/empty states."
    },
    rules: [
      {
        title: { en: "Author card strip and floating strip choose the inspected author", ru: "Author card strip и floating strip выбирают автора" },
        summary: {
          en: "The main strip selects the author for the page; the floating strip keeps that same selection available while scrolling.",
          ru: "Main strip выбирает author для страницы; floating strip сохраняет тот же selection при scrolling."
        },
        details: [
          { en: "The selected author drives the toolbar, single-row table, metrics, hourly chart, breakdown cards, and report rows.", ru: "Selected author управляет toolbar, single-row table, metrics, hourly chart, breakdown cards и report rows." }
        ],
        references: [
          { label: { en: "Author cards", ru: "Author cards" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Floating strip", ru: "Floating strip" }, href: "/activity?docTarget=activity-floating-author-strip" }
        ]
      },
      {
        title: { en: "Refresh requests a fresh report for the selected author", ru: "Refresh запрашивает fresh report выбранного автора" },
        summary: {
          en: "The selected-author toolbar can request a fresh Unity report before you decide that the displayed data is stale or wrong.",
          ru: "Selected-author toolbar может запросить fresh Unity report перед выводом, что displayed data stale или wrong."
        },
        details: [
          { en: "The button affects the current selected author, not the whole team.", ru: "Кнопка влияет на current selected author, а не на всю team." }
        ],
        references: [
          { label: { en: "Refresh action", ru: "Refresh action" }, href: "/activity?docTarget=activity-refresh-author" }
        ]
      },
      {
        title: { en: "Selected author table mirrors the Authors table for one person", ru: "Selected author table mirror Authors table для одного человека" },
        summary: {
          en: "The single-row table exposes exact columns for the selected author: day time, plugin time, active, idle, meeting, overtime, break, productivity, status, plugin, and last report.",
          ru: "Single-row table показывает exact columns selected author: day time, plugin time, active, idle, meeting, overtime, break, productivity, status, plugin и last report."
        },
        details: [
          { en: "Use it between card scanning and row-level report investigation.", ru: "Используй ее между card scanning и row-level report investigation." }
        ],
        references: [
          { label: { en: "Selected author", ru: "Selected author" }, href: "/activity?docTarget=activity-selected-author" }
        ]
      },
      {
        title: { en: "Snapshot preparing and empty day are expected states", ru: "Snapshot preparing и empty day — ожидаемые states" },
        summary: {
          en: "A historical date can show snapshot preparation; a day off can show no activity data instead of metrics.",
          ru: "Historical date может показывать snapshot preparation; day off может показывать no activity data вместо metrics."
        },
        details: [
          { en: "Use Activity Snapshots and Calendar before treating these as data loss.", ru: "Используй Activity Snapshots и Calendar перед выводом о data loss." }
        ],
        references: [
          { label: { en: "Preparing state", ru: "Preparing state" }, href: "/activity?docTarget=activity-snapshot-preparing" },
          { label: { en: "Empty day", ru: "Empty day" }, href: "/activity?docTarget=activity-empty-day" }
        ]
      },
      {
        title: { en: "Unavailable author messages are selection context", ru: "Unavailable author messages — selection context" },
        summary: {
          en: "If the selected author is not available in the current activity data, change the author or date before changing settings.",
          ru: "Если selected author недоступен в current activity data, измени author или date перед изменением settings."
        },
        details: [
          { en: "This can happen after date changes, cached restoration, or author filtering.", ru: "Это может случиться после date changes, cached restoration или author filtering." }
        ],
        references: [
          { label: { en: "Activity overview", ru: "Activity overview" }, href: "/activity?docTarget=activity-overview" }
        ]
      }
    ]
  },
  {
    id: "activity-metrics",
    title: { en: "Activity Metrics", ru: "Метрики Activity" },
    kicker: { en: "What each number means", ru: "Что означает каждое число" },
    intro: {
      en: "The Activity metrics grid separates work chat day time, plugin-reported time, active work, idle time, breaks, overtime, and productivity.",
      ru: "Сетка метрик Activity разделяет время рабочего чата, время по plugin reports, active, idle, breaks, overtime и productivity."
    },
    rules: [
      {
        title: { en: "Day Time comes from the work chat boundary", ru: "Day Time идет от границ рабочего чата" },
        summary: {
          en: "Day Time measures the declared workday window between online and offline signals, not only active editor time.",
          ru: "Day Time измеряет заявленное окно рабочего дня между online и offline, а не только активное время в редакторе."
        },
        details: [
          {
            en: "If an author closes the day, later activity is no longer allowed to reopen that same local day as a normal workday.",
            ru: "Если автор закрыл день, более поздняя активность не может заново открыть тот же локальный день как обычный рабочий день."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" }
        ]
      },
      {
        title: { en: "Plugin time explains tracked activity", ru: "Plugin time объясняет tracked activity" },
        summary: {
          en: "Plugin time is based on reports from editor and device sources, then split into active, idle, break, meeting, and overtime buckets.",
          ru: "Plugin time строится по отчетам editor и device sources, затем делится на active, idle, break, meeting и overtime buckets."
        },
        details: [
          {
            en: "Use the Plugin Reports table when a displayed number needs a row-level explanation.",
            ru: "Используй Plugin Reports, когда нужно объяснить число на уровне отдельных строк."
          }
        ],
        references: [
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" },
          { label: { en: "Send Interval", ru: "Send Interval" }, href: "/settings?tab=general&docTarget=settings-general" }
        ]
      },
      {
        title: { en: "Productivity is a ratio, not a mood", ru: "Productivity — это ratio, не настроение" },
        summary: {
          en: "Productivity compares useful active time against the relevant tracked workday context and should be read together with idle, breaks, and overtime.",
          ru: "Productivity сравнивает полезное active time с контекстом tracked workday и должна читаться вместе с idle, breaks и overtime."
        },
        details: [
          {
            en: "A high number can be good, but unusual overdrive values should be checked against the reports and hourly chart.",
            ru: "Высокое значение может быть хорошим, но необычные overdrive-значения нужно проверять через reports и часовой график."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Telegram vs FirstActivity exposes start delay", ru: "Telegram vs FirstActivity показывает задержку старта" },
        summary: {
          en: "This metric compares the work chat online time with the first real plugin activity of the day.",
          ru: "Эта метрика сравнивает время online в рабочем чате с первой реальной plugin activity дня."
        },
        details: [
          {
            en: "Use it to see whether someone opened the day long before actual tracked work started, or whether activity arrived before a clean online signal.",
            ru: "Используй ее, чтобы увидеть, открыл ли автор день сильно раньше фактической работы, или активность пришла до чистого online signal."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" }
        ]
      },
      {
        title: { en: "Active and Idle must be read together", ru: "Active и Idle нужно читать вместе" },
        summary: {
          en: "Active is useful tracked work. Idle is inactivity inside a context where the system still knows the author is in a workday or tracked session.",
          ru: "Active — полезная tracked work. Idle — неактивность внутри контекста, где система все еще знает, что автор находится в workday или tracked session."
        },
        details: [
          {
            en: "High idle is not automatically a failure; compare it with breaks, meetings, report cadence, and the hourly chart.",
            ru: "Высокий idle не всегда ошибка; сравнивай его с breaks, meetings, report cadence и часовым графиком."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Break, Meeting, and Overtime are separate explanations", ru: "Break, Meeting и Overtime — отдельные объяснения" },
        summary: {
          en: "Break time, meeting time, and overtime should not be blended into ordinary active or idle time because they explain different operational states.",
          ru: "Break time, meeting time и overtime нельзя смешивать с обычным active или idle, потому что они объясняют разные operational states."
        },
        details: [
          {
            en: "When the totals look surprising, use the hourly chart to see where each category landed during the local day.",
            ru: "Когда totals выглядят неожиданно, используй часовой график, чтобы увидеть, куда попала каждая категория в локальном дне."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Breakdown cards explain what kind of work happened", ru: "Breakdown cards объясняют тип работы" },
        summary: {
          en: "Activity Mix, Worked Files, and Overtime breakdowns explain the composition behind the raw time totals.",
          ru: "Activity Mix, Worked Files и Overtime breakdowns объясняют состав за сухими time totals."
        },
        details: [
          {
            en: "Use them after checking total time: they show whether work was navigation, saves, editor actions, file work, or overtime-specific activity.",
            ru: "Используй их после проверки total time: они показывают, была ли работа navigation, saves, editor actions, file work или overtime-specific activity."
          }
        ],
        references: [
          { label: { en: "Activity breakdowns", ru: "Activity breakdowns" }, href: "/activity?docTarget=activity-breakdowns" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      }
    ]
  },
  {
    id: "hourly-chart",
    title: { en: "Hourly Activity Chart", ru: "Часовой график Activity" },
    kicker: { en: "The complete fill rules", ru: "Полные правила заполнения" },
    intro: {
      en: "The hourly chart is the visual timeline of an author’s local day. It shows how each hour is filled by tracked work, overtime, AFK, meetings, idle time, and allowed missed time.",
      ru: "Часовой график — визуальная timeline локального дня автора. Он показывает, как каждый час заполнен tracked work, overtime, AFK, meetings, idle time и допустимым missed time."
    },
    rules: [
      {
        title: { en: "Each column is one local hour", ru: "Каждая колонка — один локальный час" },
        summary: {
          en: "The chart is per author and uses that author’s local date and timezone. A column can be empty, partial, or stacked with several visible segments.",
          ru: "График строится по автору и использует локальную дату и timezone этого автора. Колонка может быть пустой, частичной или состоять из нескольких видимых сегментов."
        },
        details: [
          {
            en: "A full column represents up to sixty minutes. A partial current hour can be highlighted because it is still in progress.",
            ru: "Полная колонка означает до шестидесяти минут. Текущий неполный час может подсвечиваться, потому что он еще продолжается."
          },
          {
            en: "The tooltip summarizes the visible minutes in that hour, so it is the fastest way to inspect a specific column.",
            ru: "Tooltip суммирует видимые минуты в этом часе, поэтому это самый быстрый способ проверить конкретную колонку."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "The chart always normalizes to 24 hours", ru: "График всегда нормализуется до 24 часов" },
        summary: {
          en: "Every local day is rendered as a full 00:00-23:00 sequence, even when some hours have no activity.",
          ru: "Каждый локальный день отображается полной последовательностью 00:00-23:00, даже если в части часов нет активности."
        },
        details: [
          {
            en: "Missing hour data appears as an empty column, not as an error. Hours outside the local-day range are ignored.",
            ru: "Отсутствующие hour data выглядят как пустая колонка, а не как ошибка. Часы вне local-day range игнорируются."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" }
        ]
      },
      {
        title: { en: "Visible colors have strict meanings", ru: "У цветов строгие значения" },
        summary: {
          en: "Active is tracked work, Overtime is work outside the normal day, AFK is manual or automatic break time, Meeting is Discord meeting time, Idle is inactivity inside a tracked context, and Missed is an allowed visual gap.",
          ru: "Active — tracked work, Overtime — работа вне обычного дня, AFK — ручной или автоматический break, Meeting — время Discord-встреч, Idle — неактивность внутри tracked context, Missed — допустимый визуальный gap."
        },
        details: [
          {
            en: "Automatic AFK is displayed as AFK. Work-chat idle is displayed as Idle. Visual overtime fill is displayed as Overtime.",
            ru: "Automatic AFK отображается как AFK. Work-chat idle отображается как Idle. Visual overtime fill отображается как Overtime."
          },
          {
            en: "The chart should not invent a category to hide missing accounting. If a normal working-hour column looks wrong, inspect reports and status first.",
            ru: "График не должен придумывать категорию, чтобы скрыть недостающий accounting. Если колонка обычного рабочего часа выглядит неверно, сначала проверяй reports и status."
          }
        ],
        references: [
          { label: { en: "Hourly legend", ru: "Легенда Hourly" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Segments are positioned by seconds inside the hour", ru: "Сегменты ставятся по секундам внутри часа" },
        summary: {
          en: "Each visible segment has a start and end position inside its hour, so stacked bars can show partial work, breaks, meetings, idle, and overtime in the same column.",
          ru: "У каждого видимого сегмента есть start и end position внутри часа, поэтому stacked bars могут показывать partial work, breaks, meetings, idle и overtime в одной колонке."
        },
        details: [
          {
            en: "Segment bounds are limited to the hour. Invalid or zero-length segments are not shown.",
            ru: "Границы сегментов ограничены пределами часа. Невалидные или zero-length сегменты не показываются."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Reports table", ru: "Reports table" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Only known segment kinds are displayed", ru: "Показываются только известные типы сегментов" },
        summary: {
          en: "The chart accepts only the supported visual categories, so unexpected activity kinds do not create new colors or misleading bars.",
          ru: "График принимает только поддерживаемые visual categories, поэтому неожиданные activity kinds не создают новые цвета или misleading bars."
        },
        details: [
          {
            en: "If data exists in reports but does not appear on the chart, first check whether it maps to one of the documented chart categories.",
            ru: "Если data есть в reports, но не видна на графике, сначала проверь, мапится ли она в одну из documented chart categories."
          }
        ],
        references: [
          { label: { en: "Hourly legend", ru: "Легенда Hourly" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Reports table", ru: "Reports table" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Missed is visual-only and narrowly allowed", ru: "Missed — только визуальный и строго ограниченный" },
        summary: {
          en: "Missed can appear before a workday starts or after an explicit offline or sign-off. It must not be used to fill holes inside an active workday.",
          ru: "Missed может появляться до начала рабочего дня или после явного offline/sign-off. Его нельзя использовать, чтобы заполнять дыры внутри активного рабочего дня."
        },
        details: [
          {
            en: "Inside an active workday, gaps should be explained by real categories: active, idle, AFK, meeting, overtime, or a reports-stopped failure.",
            ru: "Внутри активного рабочего дня gaps должны объясняться реальными категориями: active, idle, AFK, meeting, overtime или reports-stopped failure."
          },
          {
            en: "This rule keeps the chart honest: black missed time means the author was not expected to be actively reporting, not that accounting was incomplete.",
            ru: "Это сохраняет честность графика: черный missed означает, что от автора не ожидались active reports, а не что accounting был неполным."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Reports table", ru: "Reports table" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Tooltips are based on visible segments", ru: "Tooltips считаются по видимым сегментам" },
        summary: {
          en: "The hour tooltip summarizes the minutes represented by the segments in that column.",
          ru: "Tooltip часа суммирует минуты, представленные сегментами в этой колонке."
        },
        details: [
          {
            en: "This makes the tooltip a visual inspection aid. For final row-level evidence, use the reports table.",
            ru: "Это делает tooltip инструментом визуальной проверки. Для окончательного row-level evidence используй reports table."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Overtime fill can be visual-only", ru: "Overtime fill может быть только визуальным" },
        summary: {
          en: "Some overtime segments exist to fill the visual overtime area and should not be double-counted as extra tooltip minutes.",
          ru: "Некоторые overtime segments нужны для визуального заполнения overtime area и не должны double-count как дополнительные минуты tooltip-а."
        },
        details: [
          {
            en: "Read overtime totals from metrics and reports when exact accounting matters.",
            ru: "Когда нужен точный accounting, читай overtime totals из metrics и reports."
          }
        ],
        references: [
          { label: { en: "Overtime metric", ru: "Overtime metric" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Night overtime is 00:00-07:00 local time", ru: "Ночной overtime — 00:00-07:00 локального времени" },
        summary: {
          en: "Activity in the local 00:00-07:00 window is overtime. A workday cannot be opened normally during that period.",
          ru: "Активность в локальном окне 00:00-07:00 считается overtime. Обычный рабочий день нельзя открыть в этот период."
        },
        details: [
          {
            en: "If the author works through the night, the chart should show the work as overtime until the normal workday can be opened after 07:00.",
            ru: "Если автор работает ночью, график должен показывать эту работу как overtime до момента, когда обычный рабочий день можно открыть после 07:00."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" }
        ]
      },
      {
        title: { en: "Current-hour highlight is suppressed for stale authors", ru: "Current-hour highlight скрывается для stale авторов" },
        summary: {
          en: "The chart can highlight the latest partial active hour, but not when the author is in a stale reporting state.",
          ru: "График может подсвечивать последний partial active hour, но не когда автор находится в stale reporting state."
        },
        details: [
          {
            en: "For stale authors, first investigate reporting health instead of treating the latest partial bar as live progress.",
            ru: "Для stale authors сначала расследуй reporting health, а не считай последний partial bar live progress."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Post-offline activity stays overtime", ru: "Активность после offline остается overtime" },
        summary: {
          en: "After an explicit offline closes the local day, later plugin activity does not reopen the day. It is treated as overtime and can trigger a prompt asking whether the author is still offline or working overtime.",
          ru: "После явного offline локальный день закрыт, и более поздняя plugin activity не открывает день заново. Она считается overtime и может создать prompt: автор все еще offline или работает overtime."
        },
        details: [
          {
            en: "The offline boundary stays fixed. The prompt records the author’s answer but does not move the original offline time.",
            ru: "Граница offline остается фиксированной. Prompt записывает ответ автора, но не двигает исходное время offline."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" }
        ]
      },
      {
        title: { en: "Timezone label comes from the author context", ru: "Timezone label идет из контекста автора" },
        summary: {
          en: "The chart should be read in the author’s timezone, and the visible timezone label helps confirm which local day is being shown.",
          ru: "График нужно читать в timezone автора, а видимый timezone label помогает подтвердить, какой local day показан."
        },
        details: [
          {
            en: "If hour columns look shifted, verify the author profile timezone before assuming the reports are wrong.",
            ru: "Если hour columns выглядят сдвинутыми, проверь timezone в author profile перед тем, как считать reports ошибочными."
          }
        ],
        references: [
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Reports stopped is not Work Chat offline", ru: "Reports stopped — это не Work Chat offline" },
        summary: {
          en: "Reports stopped is a red failure state caused by missing plugin reports while a workday expects them. It is not the same thing as an author saying offline in the work chat.",
          ru: "Reports stopped — красное failure-состояние из-за отсутствия plugin reports при ожидаемом рабочем дне. Это не то же самое, что автор написал offline в рабочем чате."
        },
        details: [
          {
            en: "On the chart, reports-stopped gaps should be investigated through reports and status signals instead of being hidden as missed time.",
            ru: "На графике gaps от reports-stopped нужно расследовать через reports и status signals, а не скрывать как missed time."
          }
        ],
        references: [
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Empty charts are allowed for authors without data", ru: "Пустой график допустим для авторов без data" },
        summary: {
          en: "When no hourly activity exists, the chart can still render the day structure or show that there are no authors to display.",
          ru: "Когда hourly activity отсутствует, график все равно может показать структуру дня или состояние, что авторов для отображения нет."
        },
        details: [
          {
            en: "An empty chart should be interpreted with author selection, date selection, calendar marks, and report availability.",
            ru: "Пустой график нужно интерпретировать вместе с author selection, date selection, calendar marks и availability reports."
          }
        ],
        references: [
          { label: { en: "Activity date controls", ru: "Date controls в Activity" }, href: "/activity?docTarget=activity-overview" },
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" }
        ]
      }
    ]
  },
  {
    id: "workday",
    title: { en: "Workday & Work Chat Rules", ru: "Рабочий день и Work Chat" },
    kicker: { en: "Online, offline, overtime", ru: "Online, offline, overtime" },
    intro: {
      en: "Work chat events define when the normal workday starts and ends. They also protect the day from being reopened accidentally after an explicit offline.",
      ru: "События рабочего чата определяют начало и конец обычного рабочего дня. Они также защищают день от случайного повторного открытия после явного offline."
    },
    rules: [
      {
        title: { en: "Online opens a normal day only when allowed", ru: "Online открывает обычный день только когда это разрешено" },
        summary: {
          en: "A normal online can open the local workday after 07:00 if the day has not already been closed.",
          ru: "Обычный online может открыть локальный рабочий день после 07:00, если этот день еще не был закрыт."
        },
        details: [
          {
            en: "If the same local day already has an offline close, another online is blocked and the author is offered an overtime choice.",
            ru: "Если в этот локальный день уже был offline close, повторный online блокируется, а автору предлагается overtime choice."
          }
        ],
        references: [
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" },
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" }
        ]
      },
      {
        title: { en: "Offline closes the local day", ru: "Offline закрывает локальный день" },
        summary: {
          en: "Once an author explicitly goes offline, the local day is closed and normal day time should not be extended by later activity.",
          ru: "После явного offline локальный день закрыт, и последующая активность не должна продлевать обычное day time."
        },
        details: [
          {
            en: "A repeated offline is treated as a duplicate and should not create a second close for the same day.",
            ru: "Повторный offline считается duplicate и не должен создавать второе закрытие того же дня."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Post-offline prompt is one per author and local date", ru: "Post-offline prompt — один на автора и локальную дату" },
        summary: {
          en: "The first plugin activity after offline creates a prompt. Further activity on the same local date must not create duplicate prompts.",
          ru: "Первая plugin activity после offline создает prompt. Следующая активность в ту же локальную дату не должна создавать дубликаты."
        },
        details: [
          {
            en: "Still offline and Overtime both keep the activity in overtime. The difference is recorded so reports can explain what the author selected.",
            ru: "Still offline и Overtime оба оставляют активность в overtime. Разница записывается, чтобы reports объясняли выбор автора."
          }
        ],
        references: [
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Online before 07:00 is overtime, not a day start", ru: "Online до 07:00 — overtime, не старт дня" },
        summary: {
          en: "During 00:00-07:00 local time, online does not open a normal workday. Activity before 07:00 counts as overtime.",
          ru: "В локальное время 00:00-07:00 online не открывает обычный рабочий день. Активность до 07:00 считается overtime."
        },
        details: [
          {
            en: "After 07:00 on the next local day, a normal online can open the new workday.",
            ru: "После 07:00 следующего локального дня обычный online может открыть новый рабочий день."
          }
        ],
        references: [
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" },
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" }
        ]
      },
      {
        title: { en: "Duplicate online does not restart the day", ru: "Duplicate online не перезапускает день" },
        summary: {
          en: "If an author is already counted as online for the current local day, another online message should not reset the original start time.",
          ru: "Если автор уже считается online в текущий локальный день, повторный online не должен сбрасывать исходное start time."
        },
        details: [
          {
            en: "Use AFK and return-from-break behavior for breaks instead of treating repeated online as a new workday.",
            ru: "Для перерывов используй AFK и return-from-break behavior, а не повторный online как новый рабочий день."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" }
        ]
      },
      {
        title: { en: "Offline without online is rejected", ru: "Offline без online отклоняется" },
        summary: {
          en: "If the author has not opened a workday yet, offline cannot close anything and should not create activity rows.",
          ru: "Если автор еще не открыл рабочий день, offline нечего закрывать, и он не должен создавать activity rows."
        },
        details: [
          {
            en: "This protects reports from fake day boundaries caused by accidental messages.",
            ru: "Это защищает reports от ложных границ дня из-за случайных сообщений."
          }
        ],
        references: [
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Blocked online has Okay and overtime choices", ru: "Blocked online имеет Okay и overtime choices" },
        summary: {
          en: "When an author tries online after closing the same local day, Okay only dismisses the prompt, while the overtime choice records the author’s intent.",
          ru: "Когда автор пишет online после закрытия того же локального дня, Okay только закрывает prompt, а overtime choice записывает намерение автора."
        },
        details: [
          {
            en: "The original offline time stays fixed, and normal day time is not extended by this action.",
            ru: "Исходное offline time остается фиксированным, и normal day time этим действием не продлевается."
          }
        ],
        references: [
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" },
          { label: { en: "Reports table", ru: "Reports table" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Prompt actions belong to the prompted author", ru: "Prompt actions принадлежат автору prompt-а" },
        summary: {
          en: "Prompt buttons should be treated as author-specific decisions so another work chat user cannot answer for the wrong person.",
          ru: "Кнопки prompt-а считаются author-specific decisions, чтобы другой пользователь рабочего чата не ответил за неправильного человека."
        },
        details: [
          {
            en: "This keeps audit rows and report explanations tied to the person who actually owns the workday state.",
            ru: "Это сохраняет audit rows и report explanations привязанными к человеку, которому реально принадлежит workday state."
          }
        ],
        references: [
          { label: { en: "Work Chat settings", ru: "Настройки рабочего чата" }, href: "/settings?tab=telegram&docTarget=settings-telegram" },
          { label: { en: "Reports table", ru: "Reports table" }, href: "/activity?docTarget=plugin-reports" }
        ]
      }
    ]
  },
  {
    id: "afk-meeting",
    title: { en: "AFK, Break & Meeting", ru: "AFK, Break и Meeting" },
    kicker: { en: "Planned non-active time", ru: "Плановое не-active время" },
    intro: {
      en: "Breaks and meetings explain time that should not be judged like editor activity. They keep the day accounting more honest than treating every gap as idle.",
      ru: "Breaks и meetings объясняют время, которое нельзя оценивать как editor activity. Они делают day accounting честнее, чем если считать каждый gap idle."
    },
    rules: [
      {
        title: { en: "Manual AFK marks a break", ru: "Manual AFK отмечает break" },
        summary: {
          en: "When an author marks AFK, tracked work pauses into break time until the author comes back online or the break closes.",
          ru: "Когда автор ставит AFK, tracked work переходит в break time до возврата online или закрытия break."
        },
        details: [
          {
            en: "Duplicate AFK is guarded by a prompt so accidental repeated AFK does not distort the day.",
            ru: "Duplicate AFK защищен prompt-ом, чтобы случайный повторный AFK не искажал день."
          }
        ],
        references: [
          { label: { en: "Break metric", ru: "Break metric" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Auto Break settings", ru: "Auto Break settings" }, href: "/settings?tab=autoBreak&docTarget=settings-autoBreak" }
        ]
      },
      {
        title: { en: "Auto Break can move idle time into break", ru: "Auto Break может переносить idle в break" },
        summary: {
          en: "Auto Break settings decide whether an author’s long inactive intervals should be counted as break instead of ordinary idle.",
          ru: "Auto Break settings определяют, считать ли длинные inactive intervals автора break, а не обычным idle."
        },
        details: [
          {
            en: "The hourly chart displays automatic break time as AFK so the visual category stays readable.",
            ru: "Часовой график показывает automatic break как AFK, чтобы визуальная категория оставалась понятной."
          }
        ],
        references: [
          { label: { en: "Auto Break settings", ru: "Auto Break settings" }, href: "/settings?tab=autoBreak&docTarget=settings-autoBreak" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Discord meetings are separate from idle", ru: "Discord meetings отделены от idle" },
        summary: {
          en: "Meeting time is shown as its own category so collaboration does not look like editor inactivity.",
          ru: "Meeting time показывается отдельной категорией, чтобы совместная работа не выглядела как editor inactivity."
        },
        details: [
          {
            en: "Meeting notification and meeting summary settings control scheduled work chat reminders and generated meeting summaries.",
            ru: "Meeting Notification и Meeting Summaries управляют scheduled work chat reminders и generated meeting summaries."
          }
        ],
        references: [
          { label: { en: "Meeting Notification", ru: "Meeting Notification" }, href: "/settings?tab=meetingNotification&docTarget=settings-meetingNotification" },
          { label: { en: "Meeting Summaries", ru: "Meeting Summaries" }, href: "/settings?tab=meetingSummaries&docTarget=settings-meetingSummaries" }
        ]
      },
      {
        title: { en: "Duplicate AFK needs an explicit answer", ru: "Duplicate AFK требует явного ответа" },
        summary: {
          en: "A repeated AFK signal should be treated as a potential mistake until the author confirms whether the break is still active or should be closed.",
          ru: "Повторный AFK signal нужно считать потенциальной ошибкой, пока автор не подтвердит, break все еще активен или его нужно закрыть."
        },
        details: [
          {
            en: "This prevents one accidental message from creating an unrealistic break interval in the day timeline.",
            ru: "Это не дает одному случайному сообщению создать нереалистичный break interval в timeline дня."
          }
        ],
        references: [
          { label: { en: "Auto Break settings", ru: "Auto Break settings" }, href: "/settings?tab=autoBreak&docTarget=settings-autoBreak" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Return from AFK resumes normal accounting", ru: "Возврат из AFK возобновляет normal accounting" },
        summary: {
          en: "When the author returns, later tracked activity should stop extending the break and return to the appropriate active, idle, meeting, or overtime bucket.",
          ru: "Когда автор возвращается, последующая tracked activity перестает продлевать break и возвращается в нужный active, idle, meeting или overtime bucket."
        },
        details: [
          {
            en: "If return behavior looks wrong, compare the break metric, hourly AFK segments, and report rows around the transition time.",
            ru: "Если return behavior выглядит неверно, сравни break metric, AFK-сегменты на hourly chart и report rows рядом со временем перехода."
          }
        ],
        references: [
          { label: { en: "Break metric", ru: "Break metric" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Meeting summaries are not activity totals", ru: "Meeting summaries — не activity totals" },
        summary: {
          en: "Meeting summaries explain what happened in a meeting. Meeting time explains where collaboration appears in activity accounting.",
          ru: "Meeting summaries объясняют содержание встречи. Meeting time объясняет, где collaboration появляется в activity accounting."
        },
        details: [
          {
            en: "Use summary settings for delivery, language, format, participant rules, recordings, and retention; use Activity for time accounting.",
            ru: "Используй summary settings для delivery, language, format, participant rules, recordings и retention; Activity — для учета времени."
          }
        ],
        references: [
          { label: { en: "Meeting Summaries", ru: "Meeting Summaries" }, href: "/settings?tab=meetingSummaries&docTarget=settings-meetingSummaries" },
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" }
        ]
      }
    ]
  },
  {
    id: "reports-sources",
    title: { en: "Reports & Sources", ru: "Reports и Sources" },
    kicker: { en: "Where numbers come from", ru: "Откуда берутся числа" },
    intro: {
      en: "Reports are the row-level evidence behind activity metrics. They show source, version, author, time, active, idle, overtime, and event type.",
      ru: "Reports — это row-level evidence за метриками Activity. Они показывают source, version, author, time, active, idle, overtime и event type."
    },
    rules: [
      {
        title: { en: "Use filters to explain a specific hour", ru: "Фильтры помогают объяснить конкретный час" },
        summary: {
          en: "The reports table can filter by source and hour, which is useful when an hourly chart column needs investigation.",
          ru: "Reports table умеет фильтровать по source и hour, что полезно, когда нужно расследовать конкретную колонку часового графика."
        },
        details: [
          {
            en: "If the hourly chart and totals disagree with expectations, inspect the rows for that author, hour, and source first.",
            ru: "Если hourly chart и totals не совпадают с ожиданиями, сначала проверь строки этого автора, часа и source."
          }
        ],
        references: [
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Timezone shown in reports should match the author context", ru: "Timezone в reports должна совпадать с контекстом автора" },
        summary: {
          en: "The table prefers a specific author timezone label when available, because local-day rules depend on the author’s timezone.",
          ru: "Таблица предпочитает конкретную timezone автора, когда она доступна, потому что local-day rules зависят от timezone автора."
        },
        details: [
          {
            en: "Timezone mistakes can make online, offline, night overtime, and chart columns look shifted.",
            ru: "Ошибки timezone могут сдвигать online, offline, night overtime и колонки графика."
          }
        ],
        references: [
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Source and version identify the reporter", ru: "Source и version определяют reporter" },
        summary: {
          en: "Rows should be read with their source and version because different plugins and devices report different kinds of activity.",
          ru: "Строки нужно читать вместе с source и version, потому что разные plugins и devices отправляют разные типы activity."
        },
        details: [
          {
            en: "When one source looks suspicious, filter to that source before changing global settings or author profiles.",
            ru: "Когда один source выглядит подозрительно, сначала отфильтруй его, прежде чем менять global settings или author profiles."
          }
        ],
        references: [
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" },
          { label: { en: "Device Profiles", ru: "Device Profiles" }, href: "/settings?tab=deviceProfiles&docTarget=settings-deviceProfiles" }
        ]
      },
      {
        title: { en: "Report type explains why a row exists", ru: "Report type объясняет, зачем существует строка" },
        summary: {
          en: "Rows can represent plugin activity, status changes, work chat decisions, meeting signals, or audit-style explanations.",
          ru: "Строки могут означать plugin activity, status changes, work chat decisions, meeting signals или audit-style explanations."
        },
        details: [
          {
            en: "Do not read every row as editor work. Some rows explain state transitions that affect the chart and totals.",
            ru: "Не читай каждую строку как editor work. Некоторые строки объясняют state transitions, влияющие на график и totals."
          }
        ],
        references: [
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" },
          { label: { en: "Workday rules", ru: "Правила рабочего дня" }, href: "/documentation#workday" }
        ]
      },
      {
        title: { en: "Active, idle, and overtime columns are row contributions", ru: "Active, idle и overtime columns — вклад строки" },
        summary: {
          en: "The table shows how each row contributes to totals; it is not a replacement for the full-day summary cards.",
          ru: "Таблица показывает вклад каждой строки в totals; она не заменяет full-day summary cards."
        },
        details: [
          {
            en: "For totals, read the metrics and chart first. For the reason behind a specific total, inspect the rows.",
            ru: "Для totals сначала смотри metrics и chart. Для причины конкретного total смотри строки."
          }
        ],
        references: [
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Pagination means missing rows may be on another page", ru: "Pagination значит, что строки могут быть на другой странице" },
        summary: {
          en: "A filtered report table may still span multiple pages, so page controls are part of the investigation flow.",
          ru: "Даже отфильтрованная reports table может занимать несколько страниц, поэтому page controls — часть расследования."
        },
        details: [
          {
            en: "If a source or hour filter finds data, check the page count before concluding that a transition or event is missing.",
            ru: "Если source или hour filter нашел данные, проверь page count, прежде чем решать, что transition или event отсутствует."
          }
        ],
        references: [
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      }
    ]
  },
  {
    id: "analytics-calendar-complete",
    title: { en: "Analytics Complete", ru: "Analytics полностью" },
    kicker: { en: "Trends, yearly context, comparisons", ru: "Trends, yearly context, comparisons" },
    intro: {
      en: "Analytics explains trends over time by author, year, month, week, day, productivity, and stacked activity categories.",
      ru: "Analytics объясняет trends во времени по author, year, month, week, day, productivity и stacked activity categories."
    },
    rules: [
      {
        title: { en: "Analytics author selector drives yearly charts", ru: "Analytics author selector управляет yearly charts" },
        summary: {
          en: "The author strip chooses the subject; month cards show active months, weeks show active workweeks, and day charts normalize hours.",
          ru: "Author strip выбирает subject; month cards показывают active months, weeks показывают active workweeks, day charts normalize hours."
        },
        details: [
          { en: "Charts use the shared legend: Active, Overtime, AFK, Meeting, and Idle; tooltips carry exact period totals.", ru: "Charts используют shared legend: Active, Overtime, AFK, Meeting и Idle; tooltips несут exact period totals." }
        ],
        references: [
          { label: { en: "Analytics authors", ru: "Analytics authors" }, href: "/analytics?docTarget=analytics-author-strip" },
          { label: { en: "Analytics charts", ru: "Analytics charts" }, href: "/analytics?docTarget=analytics-charts" }
        ]
      },
      {
        title: { en: "Analytics empty state means no comparable yearly rows", ru: "Analytics empty state означает отсутствие comparable yearly rows" },
        summary: {
          en: "No analytics data means the yearly analytics summary has no rows to display for the current user context.",
          ru: "No analytics data означает, что yearly analytics summary не имеет rows для текущего user context."
        },
        details: [
          { en: "Use Activity and Snapshots to verify whether daily source data exists.", ru: "Используй Activity и Snapshots, чтобы проверить daily source data." }
        ],
        references: [
          { label: { en: "Analytics empty state", ru: "Analytics empty state" }, href: "/analytics?docTarget=analytics-empty-state" }
        ]
      },
      {
        title: { en: "Yearly context keeps comparisons consistent", ru: "Yearly context сохраняет comparisons consistent" },
        summary: {
          en: "Analytics is organized around the current analytics year, so month, week, and day cards are read as parts of one comparable period.",
          ru: "Analytics организован вокруг current analytics year, поэтому month, week и day cards читаются как части одного comparable period."
        },
        details: [
          { en: "Use Activity when you need a row-level explanation for one suspicious day.", ru: "Используй Activity, когда нужен row-level explanation одного подозрительного дня." }
        ],
        references: [
          { label: { en: "Analytics charts", ru: "Analytics charts" }, href: "/analytics?docTarget=analytics-charts" },
          { label: { en: "Activity reports", ru: "Activity reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Productivity badges need activity context", ru: "Productivity badges нужен activity context" },
        summary: {
          en: "A productivity badge summarizes a period, but the cause behind a change can be overtime, idle, AFK, meeting, source gaps, or calendar context.",
          ru: "Productivity badge summarises period, но причина изменения может быть overtime, idle, AFK, meeting, source gaps или calendar context."
        },
        details: [
          { en: "Use the stacked bars and then drill down to Activity before changing process expectations.", ru: "Используй stacked bars и затем drill down в Activity перед изменением process expectations." }
        ],
        references: [
          { label: { en: "Analytics charts", ru: "Analytics charts" }, href: "/analytics?docTarget=analytics-charts" },
          { label: { en: "Activity metrics", ru: "Activity metrics" }, href: "/activity?docTarget=activity-metrics" }
        ]
      },
      {
        title: { en: "Stacked bars keep categories visible", ru: "Stacked bars сохраняют categories visible" },
        summary: {
          en: "Analytics bars separate Active, Overtime, AFK, Meeting, and Idle so a trend does not hide what kind of time changed.",
          ru: "Analytics bars разделяют Active, Overtime, AFK, Meeting и Idle, чтобы trend не скрывал какой тип времени изменился."
        },
        details: [
          { en: "Use the legend before comparing authors or periods.", ru: "Используй legend перед сравнением authors или periods." }
        ],
        references: [
          { label: { en: "Analytics charts", ru: "Analytics charts" }, href: "/analytics?docTarget=analytics-charts" },
          { label: { en: "Hourly chart rules", ru: "Hourly chart rules" }, href: "/documentation#hourly-chart" }
        ]
      },
      {
        title: { en: "Workday-only weekly days are not a calendar editor", ru: "Workday-only weekly days — не calendar editor" },
        summary: {
          en: "Analytics can summarize workday-shaped weekly days, but planned exceptions and notes are maintained on Calendar.",
          ru: "Analytics может summarize workday-shaped weekly days, но planned exceptions и notes управляются на Calendar."
        },
        details: [
          { en: "Use Calendar when a period looks unusual because of vacation, days off, or planned marks.", ru: "Используй Calendar, когда period выглядит unusual из-за vacation, days off или planned marks." }
        ],
        references: [
          { label: { en: "Analytics charts", ru: "Analytics charts" }, href: "/analytics?docTarget=analytics-charts" },
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" }
        ]
      }
    ]
  },
  {
    id: "analytics-alerts",
    title: { en: "Analytics & Alerts", ru: "Analytics и Alerts" },
    kicker: { en: "Trends and exceptions", ru: "Тренды и исключения" },
    intro: {
      en: "Analytics is for comparing patterns over time. Alerts is reserved for operational issues and exception workflows.",
      ru: "Analytics нужен для сравнения паттернов во времени. Alerts зарезервирован для operational issues и exception workflows."
    },
    rules: [
      {
        title: { en: "Analytics compares authors and periods", ru: "Analytics сравнивает авторов и периоды" },
        summary: {
          en: "Use Analytics when the question is about trends, regressions, period comparisons, or team-level patterns rather than one author’s current day.",
          ru: "Используй Analytics, когда вопрос про trends, regressions, сравнение периодов или team-level patterns, а не про текущий день одного автора."
        },
        details: [
          {
            en: "When a trend looks suspicious, return to Activity and inspect the author’s hourly chart and report rows for the relevant day.",
            ru: "Если trend выглядит подозрительно, вернись в Activity и проверь hourly chart и report rows автора за нужный день."
          }
        ],
        references: [
          { label: { en: "Analytics", ru: "Analytics" }, href: "/analytics?docTarget=analytics-overview" },
          { label: { en: "Activity", ru: "Activity" }, href: "/activity?docTarget=activity-author-cards" }
        ]
      },
      {
        title: { en: "Alerts should stay focused on action", ru: "Alerts должны быть action-focused" },
        summary: {
          en: "Alerts is the place for issues that need attention, not a duplicate of every dashboard metric.",
          ru: "Alerts — место для проблем, требующих внимания, а не дубль всех dashboard metrics."
        },
        details: [
          {
            en: "Use it as the operational queue when a state needs a human decision or follow-up.",
            ru: "Используй Alerts как operational queue, когда состоянию нужно human decision или follow-up."
          }
        ],
        references: [
          { label: { en: "Alerts", ru: "Alerts" }, href: "/alerts?docTarget=alerts-overview" }
        ]
      },
      {
        title: { en: "Trend questions belong in Analytics", ru: "Trend-вопросы относятся к Analytics" },
        summary: {
          en: "Use Analytics for questions about whether productivity, active time, overtime, or idle patterns changed over a period.",
          ru: "Используй Analytics для вопросов о том, изменились ли productivity, active time, overtime или idle patterns за период."
        },
        details: [
          {
            en: "A trend can point to a problem, but the cause still needs Activity, Calendar, Settings, or Reports context.",
            ru: "Trend может указать на проблему, но причина все равно требует контекста Activity, Calendar, Settings или Reports."
          }
        ],
        references: [
          { label: { en: "Analytics", ru: "Analytics" }, href: "/analytics?docTarget=analytics-overview" },
          { label: { en: "Activity", ru: "Activity" }, href: "/activity?docTarget=activity-author-cards" }
        ]
      },
      {
        title: { en: "Alerts are not a historical analytics page", ru: "Alerts — не historical analytics page" },
        summary: {
          en: "Alerts should describe states that need attention now or soon, while Analytics explains broader historical patterns.",
          ru: "Alerts должны описывать состояния, требующие внимания сейчас или скоро, а Analytics объясняет более широкие historical patterns."
        },
        details: [
          {
            en: "If an issue needs a human decision, it belongs closer to Alerts. If it needs comparison over time, it belongs closer to Analytics.",
            ru: "Если проблеме нужен human decision, она ближе к Alerts. Если нужно сравнение во времени, она ближе к Analytics."
          }
        ],
        references: [
          { label: { en: "Alerts", ru: "Alerts" }, href: "/alerts?docTarget=alerts-overview" },
          { label: { en: "Analytics", ru: "Analytics" }, href: "/analytics?docTarget=analytics-overview" }
        ]
      },
      {
        title: { en: "Drill down before changing process", ru: "Перед изменением процесса нужен drill down" },
        summary: {
          en: "A bad-looking team trend should be traced to authors, days, hours, and report sources before changing work rules.",
          ru: "Плохой team trend нужно проследить до authors, days, hours и report sources, прежде чем менять рабочие правила."
        },
        details: [
          {
            en: "This prevents configuration, timezone, vacation, or source problems from being mistaken for a team behavior problem.",
            ru: "Это не дает спутать configuration, timezone, vacation или source problems с проблемой поведения команды."
          }
        ],
        references: [
          { label: { en: "Analytics", ru: "Analytics" }, href: "/analytics?docTarget=analytics-overview" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      }
    ]
  },
  {
    id: "calendar-complete",
    title: { en: "Calendar Complete", ru: "Calendar полностью" },
    kicker: { en: "Marks, reasons, ranges, stats", ru: "Marks, reasons, ranges, stats" },
    intro: {
      en: "Calendar explains planned exceptions by author, date, reason, note, visible month grid, and editable mark workflow.",
      ru: "Calendar объясняет planned exceptions по author, date, reason, note, visible month grid и editable mark workflow."
    },
    rules: [
      {
        title: { en: "Calendar author filter decides mark scope", ru: "Calendar author filter задает scope marks" },
        summary: {
          en: "All authors shows every mark; selecting one author filters visible marks, stats, and modal preselection.",
          ru: "All authors показывает все marks; selecting one author фильтрует visible marks, stats и modal preselection."
        },
        details: [
          { en: "Calendar marks explain days; they do not create plugin activity.", ru: "Calendar marks объясняют days; они не создают plugin activity." }
        ],
        references: [
          { label: { en: "Calendar author filter", ru: "Calendar author filter" }, href: "/calendar?docTarget=calendar-author-filter" }
        ]
      },
      {
        title: { en: "Month grid locks past days and marks selectable days", ru: "Month grid locks past days и marks selectable days" },
        summary: {
          en: "Past days are locked, today is labelled, marks appear as author-colored dots, and tooltips show mark labels and notes.",
          ru: "Past days locked, today labeled, marks отображаются author-colored dots, tooltips показывают labels и notes."
        },
        details: [
          { en: "Use month grid for exact dates and stats for totals by author/reason.", ru: "Используй month grid для exact dates и stats для totals by author/reason." }
        ],
        references: [
          { label: { en: "Month grid", ru: "Month grid" }, href: "/calendar?docTarget=calendar-month-grid" },
          { label: { en: "Calendar stats", ru: "Calendar stats" }, href: "/calendar?docTarget=calendar-stats" }
        ]
      },
      {
        title: { en: "Range select, reasons, mark days, and clear marks are the write workflow", ru: "Range select, reasons, mark days и clear marks — write workflow" },
        summary: {
          en: "Range mode or Shift creates date ranges; reason chips apply labels; Mark days saves author/date/reason/note; Clear marks removes selected author/date marks.",
          ru: "Range mode или Shift создает date ranges; reason chips применяют labels; Mark days сохраняет author/date/reason/note; Clear marks удаляет selected author/date marks."
        },
        details: [
          { en: "Clear selection resets selected dates and range start.", ru: "Clear selection сбрасывает selected dates и range start." }
        ],
        references: [
          { label: { en: "Calendar toolbar", ru: "Calendar toolbar" }, href: "/calendar?docTarget=calendar-toolbar" },
          { label: { en: "Reasons", ru: "Reasons" }, href: "/calendar?docTarget=calendar-reasons" }
        ]
      },
      {
        title: { en: "Calendar empty state means summary data is unavailable", ru: "Calendar empty state означает unavailable summary data" },
        summary: {
          en: "When Calendar has no summary payload, it shows an empty state instead of author filters, month cards, reasons, and editors.",
          ru: "Когда Calendar не имеет summary payload, он показывает empty state вместо author filters, month cards, reasons и editors."
        },
        details: [
          { en: "Check backend health or refresh before assuming marks were removed.", ru: "Проверь backend health или refresh перед выводом, что marks удалены." }
        ],
        references: [
          { label: { en: "Calendar empty state", ru: "Calendar empty state" }, href: "/calendar?docTarget=calendar-empty-state" }
        ]
      }
    ]
  },
  {
    id: "calendar-overrides",
    title: { en: "Calendar & Overrides", ru: "Calendar и Overrides" },
    kicker: { en: "Why a day is different", ru: "Почему день отличается" },
    intro: {
      en: "Calendar marks explain planned exceptions so activity cards and historical views are read with the right context.",
      ru: "Calendar marks объясняют плановые исключения, чтобы карточки Activity и historical views читались в правильном контексте."
    },
    rules: [
      {
        title: { en: "Vacation marks change expectations", ru: "Vacation marks меняют ожидания" },
        summary: {
          en: "A vacation day should not be evaluated like a normal working day, even if the author has little or no activity.",
          ru: "Vacation day не должен оцениваться как обычный рабочий день, даже если у автора мало активности или ее нет."
        },
        details: [
          {
            en: "If activity exists on a vacation day, read it as exceptional work, often overtime-like context, rather than a normal schedule.",
            ru: "Если в vacation day есть активность, ее нужно читать как исключительную работу, часто в overtime-like context, а не как обычный schedule."
          }
        ],
        references: [
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" },
          { label: { en: "Activity cards", ru: "Карточки Activity" }, href: "/activity?docTarget=activity-author-cards" }
        ]
      },
      {
        title: { en: "Calendar notes explain historical anomalies", ru: "Calendar notes объясняют historical anomalies" },
        summary: {
          en: "Use notes and reason labels when a day should be interpreted differently later.",
          ru: "Используй notes и reason labels, когда день позже нужно интерпретировать иначе."
        },
        details: [
          {
            en: "Good calendar context reduces false investigations into empty or unusual activity days.",
            ru: "Хороший calendar context уменьшает ложные расследования пустых или необычных дней."
          }
        ],
        references: [
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" }
        ]
      },
      {
        title: { en: "Author selection decides who the mark affects", ru: "Выбор автора определяет, кого затрагивает mark" },
        summary: {
          en: "Calendar marks should be checked against the selected author or team context before interpreting cards and historical days.",
          ru: "Calendar marks нужно проверять относительно выбранного автора или team context перед интерпретацией карточек и historical days."
        },
        details: [
          {
            en: "A mark for one author should not be treated as a team-wide exception unless the Calendar page explicitly shows that scope.",
            ru: "Mark одного автора не нужно считать team-wide exception, если Calendar явно не показывает такой scope."
          }
        ],
        references: [
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" },
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" }
        ]
      },
      {
        title: { en: "Clearing a mark restores normal expectations", ru: "Удаление mark возвращает normal expectations" },
        summary: {
          en: "When a vacation or override mark is removed, the day should again be reviewed as a normal tracked day unless another rule applies.",
          ru: "Когда vacation или override mark удален, день снова нужно читать как normal tracked day, если не действует другое правило."
        },
        details: [
          {
            en: "After clearing historical marks, compare Activity and Analytics again so old assumptions do not remain in the review.",
            ru: "После удаления historical marks снова сравни Activity и Analytics, чтобы старые assumptions не остались в review."
          }
        ],
        references: [
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" },
          { label: { en: "Analytics", ru: "Analytics" }, href: "/analytics?docTarget=analytics-overview" }
        ]
      },
      {
        title: { en: "Calendar is context, not raw activity", ru: "Calendar — context, не raw activity" },
        summary: {
          en: "Calendar marks explain how to read a day; they do not replace report rows or create plugin activity by themselves.",
          ru: "Calendar marks объясняют, как читать день; они не заменяют report rows и сами по себе не создают plugin activity."
        },
        details: [
          {
            en: "Use Calendar for planned exceptions, Activity for the actual timeline, and Reports for row-level evidence.",
            ru: "Используй Calendar для planned exceptions, Activity для фактической timeline, Reports для row-level evidence."
          }
        ],
        references: [
          { label: { en: "Calendar", ru: "Calendar" }, href: "/calendar?docTarget=calendar-overview" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      }
    ]
  },
  {
    id: "alerts-complete",
    title: { en: "Alerts Complete", ru: "Alerts полностью" },
    kicker: { en: "Operational issue queue", ru: "Operational issue queue" },
    intro: {
      en: "Alerts is the page for states that need attention, follow-up, or a human decision rather than broad historical comparison.",
      ru: "Alerts — страница для состояний, которым нужны attention, follow-up или human decision, а не broad historical comparison."
    },
    rules: [
      {
        title: { en: "Alerts should stay focused on action", ru: "Alerts должны быть action-focused" },
        summary: {
          en: "Alerts is the place for issues that need attention, not a duplicate of every dashboard metric.",
          ru: "Alerts — место для проблем, требующих внимания, а не дубль всех dashboard metrics."
        },
        details: [
          { en: "Use it as the operational queue when a state needs a human decision or follow-up.", ru: "Используй Alerts как operational queue, когда состоянию нужно human decision или follow-up." }
        ],
        references: [
          { label: { en: "Alerts", ru: "Alerts" }, href: "/alerts?docTarget=alerts-overview" }
        ]
      },
      {
        title: { en: "Alerts are not historical analytics", ru: "Alerts — не historical analytics" },
        summary: {
          en: "Alerts should describe states that need attention now or soon, while Analytics explains broader historical patterns.",
          ru: "Alerts должны описывать состояния, требующие внимания сейчас или скоро, а Analytics объясняет более широкие historical patterns."
        },
        details: [
          { en: "If an issue needs comparison over time, start in Analytics; if it needs a decision, keep it close to Alerts.", ru: "Если problem требует comparison over time, начни в Analytics; если нужно decision, держи ее ближе к Alerts." }
        ],
        references: [
          { label: { en: "Alerts", ru: "Alerts" }, href: "/alerts?docTarget=alerts-overview" },
          { label: { en: "Analytics", ru: "Analytics" }, href: "/analytics?docTarget=analytics-overview" }
        ]
      },
      {
        title: { en: "Alert investigation still drills into evidence", ru: "Alert investigation все равно идет в evidence" },
        summary: {
          en: "A visible issue should be checked against Activity, hourly chart context, report rows, Calendar marks, and Settings before changing process.",
          ru: "Visible issue нужно проверить через Activity, hourly chart context, report rows, Calendar marks и Settings перед изменением process."
        },
        details: [
          { en: "This prevents source, timezone, vacation, or configuration problems from being mistaken for behavior problems.", ru: "Это не дает спутать source, timezone, vacation или configuration problems с behavior problems." }
        ],
        references: [
          { label: { en: "Activity", ru: "Activity" }, href: "/activity?docTarget=activity-author-cards" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Empty Alerts means no visible issue queue", ru: "Empty Alerts означает отсутствие visible issue queue" },
        summary: {
          en: "When Alerts has no visible items, continue using Authors, Activity, Analytics, and Calendar for normal inspection.",
          ru: "Когда Alerts не показывает visible items, используй Authors, Activity, Analytics и Calendar для обычной inspection."
        },
        details: [
          { en: "No alert does not mean no activity; it means no current alert workflow is visible on that page.", ru: "No alert не означает no activity; это означает, что на этой странице нет current alert workflow." }
        ],
        references: [
          { label: { en: "Alerts", ru: "Alerts" }, href: "/alerts?docTarget=alerts-overview" },
          { label: { en: "Authors", ru: "Authors" }, href: "/authors?docTarget=authors-overview" }
        ]
      }
    ]
  },
  {
    id: "settings-complete",
    title: { en: "Settings Complete", ru: "Settings полностью" },
    kicker: { en: "Every tab and control group", ru: "Каждая tab и control group" },
    intro: {
      en: "Settings is the operational control room for intervals, server state, identities, integrations, summaries, snapshots, fake online, users, and maintenance.",
      ru: "Settings — operational control room для intervals, server state, identities, integrations, summaries, snapshots, fake online, users и maintenance."
    },
    rules: [
      {
        title: { en: "General contains reporting cadence and server state", ru: "General содержит reporting cadence и server state" },
        summary: {
          en: "Send Interval controls global/device cadence, idle thresholds, and Plugin reports on/off; Disk Usage and Services show server capacity and runtime status.",
          ru: "Send Interval управляет global/device cadence, idle thresholds и Plugin reports on/off; Disk Usage и Services показывают capacity и runtime status."
        },
        details: [
          { en: "Service reboot is a dangerous server action, not a normal refresh.", ru: "Service reboot — dangerous server action, не обычный refresh." }
        ],
        references: [
          { label: { en: "Intervals", ru: "Intervals" }, href: "/settings?tab=general&docTarget=settings-general-intervals" },
          { label: { en: "Disk Usage", ru: "Disk Usage" }, href: "/settings?tab=general&docTarget=settings-disk-usage" },
          { label: { en: "Services", ru: "Services" }, href: "/settings?tab=general&docTarget=settings-services" }
        ]
      },
      {
        title: { en: "Author Profiles contains identity, avatars, and database maintenance", ru: "Author Profiles содержит identity, avatars и database maintenance" },
        summary: {
          en: "Profiles define raw author, display name, team, GitHub, work chat username, Discord identity, timezone display, color, plugin flag, and profile deletion.",
          ru: "Profiles задают raw author, display name, team, GitHub, work chat username, Discord identity, timezone display, color, plugin flag и profile deletion."
        },
        details: [
          { en: "The tab also manages GitHub avatar refresh cadence, per-author rebuild/delete, bulk delete, and full rebuild.", ru: "Tab также управляет GitHub avatar refresh cadence, per-author rebuild/delete, bulk delete и full rebuild." }
        ],
        references: [
          { label: { en: "Profile editor", ru: "Profile editor" }, href: "/settings?tab=authors&docTarget=settings-author-profiles-editor" },
          { label: { en: "Avatars", ru: "Avatars" }, href: "/settings?tab=authors&docTarget=settings-github-avatars" },
          { label: { en: "Database maintenance", ru: "Database maintenance" }, href: "/settings?tab=authors&docTarget=settings-database-maintenance" }
        ]
      },
      {
        title: { en: "Publisher, Device, Auto Break, and Redirect tabs resolve ownership", ru: "Publisher, Device, Auto Break и Redirect tabs resolve ownership" },
        summary: {
          en: "Publisher Profiles group device-only identities, Device Profiles map raw devices to authors, Auto Break toggles per-author break conversion, and Redirects merge raw author mistakes.",
          ru: "Publisher Profiles группируют device-only identities, Device Profiles мапят raw devices к authors, Auto Break переключает break conversion, Redirects merge raw author mistakes."
        },
        details: [
          { en: "These tabs explain why incoming activity appears under a visible person or profile.", ru: "Эти tabs объясняют, почему incoming activity появляется под visible person или profile." }
        ],
        references: [
          { label: { en: "Publisher Profiles", ru: "Publisher Profiles" }, href: "/settings?tab=publisherProfiles&docTarget=settings-publisher-profiles-panel" },
          { label: { en: "Device Profiles", ru: "Device Profiles" }, href: "/settings?tab=deviceProfiles&docTarget=settings-device-profiles-panel" },
          { label: { en: "Auto Break", ru: "Auto Break" }, href: "/settings?tab=autoBreak&docTarget=settings-auto-break-panel" },
          { label: { en: "Redirects", ru: "Redirects" }, href: "/settings?tab=redirects&docTarget=settings-author-redirects-panel" }
        ]
      },
      {
        title: { en: "Discord, Work Chat, and Meeting Notification control bot behavior", ru: "Discord, Work Chat и Meeting Notification control bot behavior" },
        summary: {
          en: "Discord sets meeting Auto-AFK timeout, Telegram sets online confirmation delay, and Meeting Notification schedules weekday work chat reminders for selected authors.",
          ru: "Discord задает meeting Auto-AFK timeout, Telegram задает online confirmation delay, Meeting Notification планирует weekday reminders выбранным authors."
        },
        details: [
          { en: "Authors without a work chat username cannot be selected for reminder mentions.", ru: "Authors без work chat username нельзя выбрать для reminder mentions." }
        ],
        references: [
          { label: { en: "Discord", ru: "Discord" }, href: "/settings?tab=discord&docTarget=settings-discord-panel" },
          { label: { en: "Telegram", ru: "Telegram" }, href: "/settings?tab=telegram&docTarget=settings-telegram-panel" },
          { label: { en: "Meeting Notification", ru: "Meeting Notification" }, href: "/settings?tab=meetingNotification&docTarget=settings-meeting-notification-panel" }
        ]
      },
      {
        title: { en: "Meeting Summaries covers settings, usage, process, prompts, and format", ru: "Meeting Summaries покрывает settings, usage, process, prompts и format" },
        summary: {
          en: "Controls include enablement, participant/duration thresholds, language, recipient, audio retention, OpenAI Stats, today's process, archive, summary instructions, and message format.",
          ru: "Controls включают enablement, participant/duration thresholds, language, recipient, audio retention, OpenAI Stats, today process, archive, summary instructions и message format."
        },
        details: [
          { en: "OpenAI stats can refresh current month or totals and can show syncing progress or errors.", ru: "OpenAI stats может refresh current month или totals и показывать syncing progress или errors." }
        ],
        references: [
          { label: { en: "Summary controls", ru: "Summary controls" }, href: "/settings?tab=meetingSummaries&docTarget=settings-meeting-summary-controls" },
          { label: { en: "OpenAI Stats", ru: "OpenAI Stats" }, href: "/settings?tab=meetingSummaries&docTarget=settings-openai-stats" },
          { label: { en: "Today process", ru: "Today process" }, href: "/settings?tab=meetingSummaries&docTarget=settings-meeting-summary-today" },
          { label: { en: "Archive", ru: "Archive" }, href: "/settings?tab=meetingSummaries&docTarget=settings-meeting-summary-archive" },
          { label: { en: "Instructions", ru: "Instructions" }, href: "/settings?tab=meetingSummaries&docTarget=settings-summary-instructions" },
          { label: { en: "Format", ru: "Format" }, href: "/settings?tab=meetingSummaries&docTarget=settings-telegram-summary-format" }
        ]
      },
      {
        title: { en: "Snapshots, Fake Online, and Site Users are admin operations", ru: "Snapshots, Fake Online и Site Users — admin operations" },
        summary: {
          en: "Snapshots rebuild historical dashboard views, Fake Online schedules controlled online prompts, and Site Users manages login accounts, roles, status, passwords, and server stats permission.",
          ru: "Snapshots rebuild historical dashboard views, Fake Online планирует controlled online prompts, Site Users управляет login accounts, roles, status, passwords и server stats permission."
        },
        details: [
          { en: "Fake Online and user management can be hidden depending on the current user's permissions.", ru: "Fake Online и user management могут быть скрыты в зависимости от permissions текущего user." }
        ],
        references: [
          { label: { en: "Snapshots", ru: "Snapshots" }, href: "/settings?tab=snapshots&docTarget=settings-snapshots-panel" },
          { label: { en: "Fake Online", ru: "Fake Online" }, href: "/settings?tab=fakeOnline&docTarget=settings-fake-online-panel" },
          { label: { en: "Site Users", ru: "Site Users" }, href: "/settings?tab=users&docTarget=settings-site-users-panel" }
        ]
      }
    ]
  },
  {
    id: "settings-maintenance",
    title: { en: "Settings & Maintenance", ru: "Settings и Maintenance" },
    kicker: { en: "Controls that change behavior", ru: "Настройки, меняющие поведение" },
    intro: {
      en: "Settings define reporting cadence, idle thresholds, author identity, integrations, snapshots, fake online behavior, user access, and maintenance operations.",
      ru: "Settings определяют reporting cadence, idle thresholds, identity авторов, integrations, snapshots, fake online behavior, доступ пользователей и maintenance operations."
    },
    rules: [
      {
        title: { en: "General controls reporting cadence", ru: "General управляет частотой reports" },
        summary: {
          en: "Global and device intervals decide expected report cadence. Idle thresholds decide when inactivity starts being treated as idle.",
          ru: "Global и device intervals задают ожидаемую частоту reports. Idle thresholds определяют, когда inactive time становится idle."
        },
        details: [
          {
            en: "Changing these settings affects future interpretation and should be checked against the Activity page after saving.",
            ru: "Изменение этих настроек влияет на будущую интерпретацию, поэтому после сохранения стоит проверить Activity."
          }
        ],
        references: [
          { label: { en: "General settings", ru: "General settings" }, href: "/settings?tab=general&docTarget=settings-general" },
          { label: { en: "Activity metrics", ru: "Метрики Activity" }, href: "/activity?docTarget=activity-metrics" }
        ]
      },
      {
        title: { en: "Author Profiles define visible people", ru: "Author Profiles задают видимых людей" },
        summary: {
          en: "Author Profiles store display names, teams, work chat usernames, Discord identities, colors, timezones, avatars, plugin flags, and per-author auto break settings.",
          ru: "Author Profiles хранят display names, teams, usernames рабочего чата, Discord identities, colors, timezones, avatars, plugin flags и per-author auto break settings."
        },
        details: [
          {
            en: "Use this tab first when a person looks wrong in cards, charts, reports, or timezone-sensitive workday rules.",
            ru: "Начинай с этой вкладки, если человек неверно выглядит в карточках, графиках, reports или timezone-sensitive правилах рабочего дня."
          }
        ],
        references: [
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" },
          { label: { en: "Author cards", ru: "Карточки авторов" }, href: "/activity?docTarget=activity-author-cards" }
        ]
      },
      {
        title: { en: "Publisher Profiles organize external profiles", ru: "Publisher Profiles организуют внешние профили" },
        summary: {
          en: "Publisher Profiles are for device-only publishers, external testers, or non-person profiles that still need a readable identity in AL.",
          ru: "Publisher Profiles нужны для device-only publishers, external testers или non-person profiles, которым все равно нужна читаемая identity в AL."
        },
        details: [
          {
            en: "Link devices here when activity should appear under a publisher-style profile instead of a normal author profile.",
            ru: "Привязывай устройства здесь, когда активность должна отображаться под publisher-style profile, а не обычным author profile."
          }
        ],
        references: [
          { label: { en: "Publisher Profiles", ru: "Publisher Profiles" }, href: "/settings?tab=publisherProfiles&docTarget=settings-publisherProfiles" },
          { label: { en: "Device Profiles", ru: "Device Profiles" }, href: "/settings?tab=deviceProfiles&docTarget=settings-deviceProfiles" }
        ]
      },
      {
        title: { en: "Device Profiles attach device activity", ru: "Device Profiles привязывают device activity" },
        summary: {
          en: "Device Profiles map raw device identities to authors so phone, editor, or device-only activity is not left detached.",
          ru: "Device Profiles связывают raw device identities с авторами, чтобы phone, editor или device-only activity не оставалась отдельно."
        },
        details: [
          {
            en: "Check this tab when device activity appears under the wrong name or needs to be merged into a visible author.",
            ru: "Проверяй эту вкладку, когда device activity отображается под неверным именем или должна объединиться с видимым автором."
          }
        ],
        references: [
          { label: { en: "Device Profiles", ru: "Device Profiles" }, href: "/settings?tab=deviceProfiles&docTarget=settings-deviceProfiles" },
          { label: { en: "Reports table", ru: "Reports table" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Auto Break controls per-author break behavior", ru: "Auto Break управляет break behavior автора" },
        summary: {
          en: "Auto Break decides whether long inactivity should be represented as break time for each author.",
          ru: "Auto Break определяет, должна ли длинная неактивность автора отображаться как break time."
        },
        details: [
          {
            en: "This affects the Break metric and the hourly chart, where automatic break time is displayed as AFK.",
            ru: "Это влияет на Break metric и часовой график, где automatic break time отображается как AFK."
          }
        ],
        references: [
          { label: { en: "Auto Break", ru: "Auto Break" }, href: "/settings?tab=autoBreak&docTarget=settings-autoBreak" },
          { label: { en: "Hourly Activity", ru: "Hourly Activity" }, href: "/activity?docTarget=hourly-activity" }
        ]
      },
      {
        title: { en: "Author Redirects merge raw names", ru: "Author Redirects объединяют raw names" },
        summary: {
          en: "Author Redirects tell AL that one raw author identity should be treated as another visible author.",
          ru: "Author Redirects говорят AL, что один raw author identity нужно считать другим видимым автором."
        },
        details: [
          {
            en: "Use redirects when reports arrive under aliases, machine names, old names, or inconsistent plugin identities.",
            ru: "Используй redirects, когда reports приходят под aliases, machine names, старыми именами или inconsistent plugin identities."
          }
        ],
        references: [
          { label: { en: "Author Redirects", ru: "Author Redirects" }, href: "/settings?tab=redirects&docTarget=settings-redirects" },
          { label: { en: "Plugin Reports", ru: "Plugin Reports" }, href: "/activity?docTarget=plugin-reports" }
        ]
      },
      {
        title: { en: "Discord controls meeting behavior", ru: "Discord управляет meeting behavior" },
        summary: {
          en: "Discord settings control meeting-related automation, including how meeting time and meeting AFK behavior enter the dashboard.",
          ru: "Discord settings управляют meeting automation, включая то, как meeting time и meeting AFK behavior попадают в dashboard."
        },
        details: [
          {
            en: "Use it when meeting time, auto AFK from voice channels, or Discord identity behavior needs adjustment.",
            ru: "Используй ее, когда нужно настроить meeting time, auto AFK из voice channels или Discord identity behavior."
          }
        ],
        references: [
          { label: { en: "Discord", ru: "Discord" }, href: "/settings?tab=discord&docTarget=settings-discord" },
          { label: { en: "Meeting metric", ru: "Meeting metric" }, href: "/activity?docTarget=activity-metrics" }
        ]
      },
      {
        title: { en: "Telegram controls Work Chat prompts", ru: "Telegram управляет Work Chat prompts" },
        summary: {
          en: "The Telegram tab controls the delay before the bot asks whether first plugin activity means the author is online or the activity was a mistake.",
          ru: "Telegram tab управляет задержкой перед тем, как бот спросит, означает ли первая plugin activity, что автор online, или это ошибка."
        },
        details: [
          {
            en: "This setting is related to online confirmation prompts, post-offline overtime prompts, and workday boundary explanations.",
            ru: "Эта настройка связана с online confirmation prompts, post-offline overtime prompts и объяснением границ рабочего дня."
          }
        ],
        references: [
          { label: { en: "Telegram", ru: "Telegram" }, href: "/settings?tab=telegram&docTarget=settings-telegram" },
          { label: { en: "Workday rules", ru: "Правила рабочего дня" }, href: "/documentation#workday" }
        ]
      },
      {
        title: { en: "Meeting Notification sends scheduled work chat reminders", ru: "Meeting Notification отправляет scheduled reminders" },
        summary: {
          en: "Meeting Notification configures scheduled work chat messages that mention selected authors and ask them to join the Discord meeting channel.",
          ru: "Meeting Notification настраивает scheduled work chat messages, которые упоминают выбранных авторов и просят зайти в Discord meeting channel."
        },
        details: [
          {
            en: "Use it for recurring meeting nudges: time, timezone, weekdays, enabled state, and selected authors.",
            ru: "Используй это для recurring meeting nudges: time, timezone, weekdays, enabled state и selected authors."
          }
        ],
        references: [
          { label: { en: "Meeting Notification", ru: "Meeting Notification" }, href: "/settings?tab=meetingNotification&docTarget=settings-meetingNotification" },
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-authors" }
        ]
      },
      {
        title: { en: "Meeting Summaries configure generated summaries", ru: "Meeting Summaries настраивает summaries" },
        summary: {
          en: "Meeting Summaries controls summary language, recipients, minimum participant and duration rules, prompt instructions, format, recordings, and OpenAI usage display.",
          ru: "Meeting Summaries управляет языком summaries, recipients, minimum participant/duration rules, prompt instructions, format, recordings и OpenAI usage display."
        },
        details: [
          {
            en: "Use it when meeting summaries are missing, sent to the wrong place, formatted badly, or using the wrong retention policy.",
            ru: "Используй это, когда meeting summaries отсутствуют, отправляются не туда, плохо форматируются или используют неверную retention policy."
          }
        ],
        references: [
          { label: { en: "Meeting Summaries", ru: "Meeting Summaries" }, href: "/settings?tab=meetingSummaries&docTarget=settings-meetingSummaries" },
          { label: { en: "Meeting Notification", ru: "Meeting Notification" }, href: "/settings?tab=meetingNotification&docTarget=settings-meetingNotification" }
        ]
      },
      {
        title: { en: "Activity Snapshots keep history stable", ru: "Activity Snapshots стабилизируют history" },
        summary: {
          en: "Snapshots make historical views faster and stable. Rebuild and delete controls should be used deliberately because they change stored activity views.",
          ru: "Snapshots делают historical views быстрее и стабильнее. Rebuild и delete controls нужно использовать осторожно, потому что они меняют stored activity views."
        },
        details: [
          {
            en: "When historical data looks stale, check snapshots before assuming the live dashboard is wrong.",
            ru: "Если historical data выглядит устаревшей, сначала проверь snapshots, прежде чем считать live dashboard ошибочным."
          }
        ],
        references: [
          { label: { en: "Activity Snapshots", ru: "Activity Snapshots" }, href: "/settings?tab=snapshots&docTarget=settings-snapshots" },
          { label: { en: "Author Profiles maintenance", ru: "Author Profiles maintenance" }, href: "/settings?tab=authors&docTarget=settings-authors" }
        ]
      },
      {
        title: { en: "Fake Online is an admin-only behavior override", ru: "Fake Online — admin-only override" },
        summary: {
          en: "Fake Online can create controlled online behavior for selected authors and should be treated as an explicit operational override.",
          ru: "Fake Online может создавать controlled online behavior для выбранных авторов и должен считаться явным operational override."
        },
        details: [
          {
            en: "It is hidden from users who cannot manage users, so documentation links should fall back to Settings if the tab is not visible.",
            ru: "Он скрыт от пользователей без user-management прав, поэтому ссылка документации должна вести в Settings, даже если tab не виден."
          }
        ],
        references: [
          { label: { en: "Fake Online", ru: "Fake Online" }, href: "/settings?tab=fakeOnline&docTarget=settings-fakeOnline" },
          { label: { en: "Site Users", ru: "Site Users" }, href: "/settings?tab=users&docTarget=settings-users" }
        ]
      },
      {
        title: { en: "Site Users controls dashboard access", ru: "Site Users управляет доступом" },
        summary: {
          en: "Site Users manages who can log into the dashboard and what role they have.",
          ru: "Site Users управляет тем, кто может входить в dashboard и какая у него роль."
        },
        details: [
          {
            en: "Use it when someone needs admin, editor, viewer, or user-management access, or when an account should be removed.",
            ru: "Используй это, когда кому-то нужен admin, editor, viewer или user-management access, либо когда аккаунт нужно удалить."
          }
        ],
        references: [
          { label: { en: "Site Users", ru: "Site Users" }, href: "/settings?tab=users&docTarget=settings-users" },
          { label: { en: "Settings", ru: "Settings" }, href: "/settings?tab=general&docTarget=settings-general" }
        ]
      }
    ]
  },
  {
    id: "maintenance-dangerous-actions",
    title: { en: "Maintenance & Dangerous Actions", ru: "Maintenance и опасные действия" },
    kicker: { en: "Confirmation, scope, consequences", ru: "Confirmation, scope, consequences" },
    intro: {
      en: "Dangerous actions can restart services, rebuild dashboard activity views, delete activity, delete profiles, delete device mappings, or remove site access.",
      ru: "Dangerous actions могут restart services, rebuild dashboard activity views, delete activity, delete profiles, delete device mappings или remove site access."
    },
    rules: [
      {
        title: { en: "Server reboot restarts the host and services", ru: "Server reboot перезапускает host и services" },
        summary: {
          en: "Reboot production server restarts the machine and services, so the dashboard can disconnect while everything comes back online.",
          ru: "Reboot production server перезапускает machine и services, поэтому dashboard может disconnect пока все возвращается online."
        },
        details: [
          { en: "Use it only when a full host restart is intended.", ru: "Используй только когда нужен full host restart." }
        ],
        references: [
          { label: { en: "Services", ru: "Services" }, href: "/settings?tab=general&docTarget=settings-services" }
        ]
      },
      {
        title: { en: "Rebuild actions recalculate dashboard activity", ru: "Rebuild actions пересчитывают dashboard activity" },
        summary: {
          en: "Scoped rebuild affects one author/date range; full rebuild recalculates dashboard activity from stored source evidence.",
          ru: "Scoped rebuild влияет на one author/date range; full rebuild пересчитывает dashboard activity из stored source evidence."
        },
        details: [
          { en: "Rebuilds keep profiles, users, settings, and original source evidence unchanged.", ru: "Rebuilds сохраняют profiles, users, settings и original source evidence unchanged." }
        ],
        references: [
          { label: { en: "Database maintenance", ru: "Database maintenance" }, href: "/settings?tab=authors&docTarget=settings-database-maintenance" }
        ]
      },
      {
        title: { en: "Delete activity actions remove scoped activity data", ru: "Delete activity actions удаляют scoped activity data" },
        summary: {
          en: "Per-author period delete removes activity in range; Delete all data removes every historical activity record for one author but keeps the profile row.",
          ru: "Per-author period delete удаляет activity in range; Delete all data удаляет всю historical activity одного author, но keeps profile row."
        },
        details: [
          { en: "Bulk delete applies the same idea to every known author in a UTC window or across full history.", ru: "Bulk delete применяет ту же идею ко всем known authors в UTC window или по full history." }
        ],
        references: [
          { label: { en: "Database maintenance", ru: "Database maintenance" }, href: "/settings?tab=authors&docTarget=settings-database-maintenance" }
        ]
      },
      {
        title: { en: "Delete profile removes identity and linked activity", ru: "Delete profile удаляет identity и linked activity" },
        summary: {
          en: "Delete profile removes the editable profile row, mappings, preferences, reports, source evidence, snapshots, work chat rows, meetings, marks, and statistics for that author.",
          ru: "Delete profile удаляет editable profile row, mappings, preferences, reports, source evidence, snapshots, work chat rows, meetings, marks и statistics этого author."
        },
        details: [
          { en: "Unlike Delete all data, the profile must be recreated manually if needed later.", ru: "В отличие от Delete all data, profile потом нужно recreate manually." }
        ],
        references: [
          { label: { en: "Author Profiles", ru: "Author Profiles" }, href: "/settings?tab=authors&docTarget=settings-author-profiles-editor" }
        ]
      },
      {
        title: { en: "Device deletes remove mappings, not historical reports", ru: "Device deletes удаляют mappings, не historical reports" },
        summary: {
          en: "Deleting one or all device profiles removes stored device mappings and author links; future reports can recreate device profiles.",
          ru: "Deleting one или all device profiles удаляет stored device mappings и author links; future reports могут recreate device profiles."
        },
        details: [
          { en: "Existing source reports and activity data remain unless deleted through activity maintenance.", ru: "Existing source reports и activity data остаются, если не удалены через activity maintenance." }
        ],
        references: [
          { label: { en: "Device Profiles", ru: "Device Profiles" }, href: "/settings?tab=deviceProfiles&docTarget=settings-device-profiles-panel" }
        ]
      },
      {
        title: { en: "Site user delete removes dashboard access", ru: "Site user delete удаляет dashboard access" },
        summary: {
          en: "Deleting a site user removes the login account and should be done only after another admin account remains available.",
          ru: "Deleting site user удаляет login account и допустим только когда другой admin account остается доступен."
        },
        details: [
          { en: "It does not delete an author profile unless that profile is deleted separately.", ru: "Это не удаляет author profile, если profile не удален отдельно." }
        ],
        references: [
          { label: { en: "Site Users", ru: "Site Users" }, href: "/settings?tab=users&docTarget=settings-site-users-panel" }
        ]
      }
    ]
  }
];
