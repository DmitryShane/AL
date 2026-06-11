import { useEffect, useMemo, useState } from "react";
import type React from "react";
import { ArrowUpRight, BookOpen, CheckCircle2, ChevronDown, Languages } from "lucide-react";
import {
  DOCUMENTATION_CATEGORIES,
  DOCUMENTATION_LANGUAGES,
  type DocumentationCategory,
  type DocumentationLanguage,
  type DocumentationRule
} from "./documentationContent";
import { localBrowserStorage, readStorageItem, writeStorageState } from "../utils/browserStorage";

const DOCUMENTATION_LANGUAGE_STORAGE_KEY = "AL.Documentation.Language";
const DOCUMENTATION_NAV_GROUPS: DocumentationNavGroup[] = [
  {
    id: "documentation",
    title: { en: "Documentation", ru: "Documentation" },
    items: [
      { categoryId: "overview", label: { en: "Product overview", ru: "Обзор продукта" } }
    ]
  },
  {
    id: "authors",
    title: { en: "Authors", ru: "Authors" },
    items: [
      { categoryId: "authors-page-complete", label: { en: "Author table, search, sorting", ru: "Таблица, search, sorting" } },
      { categoryId: "authors-status", label: { en: "Status cards and colors", ru: "Status cards и цвета" } }
    ]
  },
  {
    id: "activity",
    title: { en: "Activity", ru: "Activity" },
    items: [
      { categoryId: "navigation-login-date", label: { en: "Date selector and cached dates", ru: "Date selector и cached dates" } },
      { categoryId: "activity-ui-complete", label: { en: "Author strip, toolbar, states", ru: "Author strip, toolbar, states" } },
      { categoryId: "activity-metrics", label: { en: "Metric cards", ru: "Metric cards" } },
      { categoryId: "hourly-chart", label: { en: "Hourly Activity Chart", ru: "Hourly Activity Chart" } },
      { categoryId: "workday", label: { en: "Work Chat workday rules", ru: "Правила Work Chat" } },
      { categoryId: "afk-meeting", label: { en: "AFK, break, meeting", ru: "AFK, break, meeting" } },
      { categoryId: "reports-sources", label: { en: "Reports table and sources", ru: "Reports table и sources" } }
    ]
  },
  {
    id: "analytics",
    title: { en: "Analytics", ru: "Analytics" },
    items: [
      { categoryId: "analytics-calendar-complete", label: { en: "Author selector and charts", ru: "Author selector и charts" } },
      { categoryId: "analytics-alerts", label: { en: "Trends and alerts context", ru: "Trends и alerts context" } }
    ]
  },
  {
    id: "calendar",
    title: { en: "Calendar", ru: "Calendar" },
    items: [
      { categoryId: "calendar-complete", label: { en: "Calendar toolbar and month grid", ru: "Toolbar и month grid" } },
      { categoryId: "calendar-overrides", label: { en: "Overrides, vacation, marks", ru: "Overrides, vacation, marks" } }
    ]
  },
  {
    id: "alerts",
    title: { en: "Alerts", ru: "Alerts" },
    items: [
      { categoryId: "alerts-complete", label: { en: "Issue monitoring", ru: "Issue monitoring" } }
    ]
  },
  {
    id: "settings",
    title: { en: "Settings", ru: "Settings" },
    items: [
      { categoryId: "settings-complete", label: { en: "All 13 Settings tabs", ru: "Все 13 Settings tabs" } },
      { categoryId: "settings-maintenance", label: { en: "Control groups and maintenance", ru: "Control groups и maintenance" } },
      { categoryId: "maintenance-dangerous-actions", label: { en: "Dangerous actions and modals", ru: "Dangerous actions и modals" } }
    ]
  }
];

export function DocumentationPage() {
  const [language, setLanguage] = useState<DocumentationLanguage>(() => loadDocumentationLanguage());
  const [activeCategory, setActiveCategory] = useState(DOCUMENTATION_CATEGORIES[0]?.id ?? "overview");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set(DOCUMENTATION_NAV_GROUPS.map((group) => group.id)));

  useEffect(() => {
    writeStorageState(localBrowserStorage(), DOCUMENTATION_LANGUAGE_STORAGE_KEY, language);
  }, [language]);

  useEffect(() => {
    let frameId = 0;

    const updateActiveCategory = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(() => {
        setActiveCategory(currentActiveCategory());
      });
    };

    updateActiveCategory();
    window.addEventListener("scroll", updateActiveCategory, { passive: true });
    window.addEventListener("resize", updateActiveCategory);
    window.addEventListener("hashchange", updateActiveCategory);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("scroll", updateActiveCategory);
      window.removeEventListener("resize", updateActiveCategory);
      window.removeEventListener("hashchange", updateActiveCategory);
    };
  }, []);

  const totalRules = useMemo(
    () => DOCUMENTATION_CATEGORIES.reduce((total, category) => total + category.rules.length, 0),
    []
  );
  const activeGroupId = useMemo(() => activeDocumentationGroupId(activeCategory), [activeCategory]);

  useEffect(() => {
    if (!activeGroupId) {
      return;
    }

    setExpandedGroups((current) => {
      if (current.has(activeGroupId)) {
        return current;
      }

      const next = new Set(current);
      next.add(activeGroupId);
      return next;
    });
  }, [activeGroupId]);

  return (
    <section className="page-section documentation-page" data-doc-target="documentation-overview" id="documentation-overview">
      <div className="documentation-hero">
        <div className="documentation-hero-copy">
          <p className="documentation-eyebrow">{language === "en" ? "Operational manual" : "Операционный справочник"}</p>
          <h2>{language === "en" ? "How Activity Logger works" : "Как работает Activity Logger"}</h2>
          <p>
            {language === "en"
              ? "A practical guide to dashboard cards, workday rules, hourly chart semantics, settings, and investigation flows."
              : "Практичный справочник по карточкам dashboard, правилам рабочего дня, семантике часового графика, настройкам и расследованиям."}
          </p>
        </div>
        <div className="documentation-hero-side">
          <LanguageSwitcher language={language} onChange={setLanguage} />
          <div className="documentation-count-card" aria-label={language === "en" ? "Documentation coverage" : "Покрытие документации"}>
            <BookOpen size={20} />
            <strong>{DOCUMENTATION_CATEGORIES.length}</strong>
            <span>{language === "en" ? "categories" : "категорий"}</span>
            <strong>{totalRules}</strong>
            <span>{language === "en" ? "rules" : "правил"}</span>
          </div>
        </div>
      </div>

      <div className="documentation-layout">
        <DocumentationCategoryNav
          activeCategory={activeCategory}
          expandedGroups={expandedGroups}
          language={language}
          onToggleGroup={(groupId) => {
            setExpandedGroups((current) => {
              const next = new Set(current);

              if (next.has(groupId)) {
                next.delete(groupId);
              } else {
                next.add(groupId);
              }

              return next;
            });
          }}
        />

        <div className="documentation-category-stack">
          {DOCUMENTATION_CATEGORIES.map((category, categoryIndex) => (
            <DocumentationCategorySection
              category={category}
              categoryIndex={categoryIndex}
              language={language}
              key={category.id}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

type DocumentationNavItem = {
  categoryId: string;
  label: Record<DocumentationLanguage, string>;
};

type DocumentationNavGroup = {
  id: string;
  title: Record<DocumentationLanguage, string>;
  items: DocumentationNavItem[];
};

function DocumentationCategoryNav({
  activeCategory,
  expandedGroups,
  language,
  onToggleGroup
}: {
  activeCategory: string;
  expandedGroups: Set<string>;
  language: DocumentationLanguage;
  onToggleGroup: (groupId: string) => void;
}) {
  return (
    <nav className="documentation-category-nav" aria-label={language === "en" ? "Documentation navigation by page" : "Навигация документации по страницам"}>
      <div className="documentation-nav-title">
        <span>{language === "en" ? "Site pages" : "Страницы сайта"}</span>
        <small>{language === "en" ? "Open a page, then pick its card or rule group." : "Открой страницу, затем выбери ее карточку или группу правил."}</small>
      </div>
      {DOCUMENTATION_NAV_GROUPS.map((group) => {
        const isOpen = expandedGroups.has(group.id);
        const isGroupActive = group.items.some((item) => item.categoryId === activeCategory);

        return (
          <div className={isGroupActive ? "documentation-nav-group active" : "documentation-nav-group"} key={group.id}>
            <button
              aria-expanded={isOpen}
              className="documentation-nav-group-button"
              onClick={() => onToggleGroup(group.id)}
              type="button"
            >
              <ChevronDown size={15} />
              <span>{group.title[language]}</span>
            </button>
            {isOpen ? (
              <div className="documentation-nav-subitems">
                {group.items.map((item) => {
                  const category = DOCUMENTATION_CATEGORIES.find((candidate) => candidate.id === item.categoryId);

                  return (
                    <a
                      className={activeCategory === item.categoryId ? "active" : ""}
                      href={`#${item.categoryId}`}
                      key={`${group.id}-${item.categoryId}-${item.label.en}`}
                    >
                      <span>{item.label[language]}</span>
                      {category ? <small>{category.rules.length}</small> : null}
                    </a>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
    </nav>
  );
}

function LanguageSwitcher({
  language,
  onChange
}: {
  language: DocumentationLanguage;
  onChange: (language: DocumentationLanguage) => void;
}) {
  return (
    <div className="documentation-language-switcher" aria-label="Documentation language">
      <Languages size={16} />
      {DOCUMENTATION_LANGUAGES.map((item) => (
        <button
          className={language === item.key ? "active" : ""}
          key={item.key}
          onClick={() => onChange(item.key)}
          type="button"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function DocumentationCategorySection({
  category,
  categoryIndex,
  language
}: {
  category: DocumentationCategory;
  categoryIndex: number;
  language: DocumentationLanguage;
}) {
  return (
    <section
      className="documentation-category"
      id={category.id}
      style={{ "--documentation-category-index": categoryIndex } as React.CSSProperties}
    >
      <div className="documentation-category-header">
        <div className="documentation-category-marker" aria-hidden="true">
          {String(categoryIndex + 1).padStart(2, "0")}
        </div>
        <div className="documentation-category-heading">
          <p>{category.kicker[language]}</p>
          <h3>{category.title[language]}</h3>
          <span>{category.intro[language]}</span>
        </div>
        <div className="documentation-category-rule-count">
          <strong>{category.rules.length}</strong>
          <span>{language === "en" ? "rules" : "правил"}</span>
        </div>
      </div>
      <div className="documentation-rule-grid">
        {category.rules.map((rule, index) => (
          <DocumentationRuleCard
            language={language}
            rule={rule}
            index={index}
            key={rule.title.en}
          />
        ))}
      </div>
    </section>
  );
}

function DocumentationRuleCard({
  rule,
  language,
  index
}: {
  rule: DocumentationRule;
  language: DocumentationLanguage;
  index: number;
}) {
  return (
    <article
      className="documentation-rule-card"
      style={{ "--documentation-rule-index": index } as React.CSSProperties}
    >
      <div className="documentation-rule-card-icon" aria-hidden="true">
        <CheckCircle2 size={18} />
      </div>
      <div className="documentation-rule-card-copy">
        <h4>{rule.title[language]}</h4>
        <p>{rule.summary[language]}</p>
        <ul>
          {rule.details.map((detail) => (
            <li key={detail.en}>{detail[language]}</li>
          ))}
        </ul>
      </div>
      <div className="documentation-reference-row">
        {rule.references.map((reference) => (
          <a href={reference.href} key={`${reference.href}-${reference.label.en}`}>
            <span>{reference.label[language]}</span>
            <ArrowUpRight size={14} />
          </a>
        ))}
      </div>
    </article>
  );
}

function loadDocumentationLanguage(): DocumentationLanguage {
  const stored = readStorageItem(localBrowserStorage(), DOCUMENTATION_LANGUAGE_STORAGE_KEY);

  if (stored === "ru" || stored === "en") {
    return stored;
  }

  return "en";
}

function activeDocumentationGroupId(categoryId: string) {
  return DOCUMENTATION_NAV_GROUPS.find((group) => group.items.some((item) => item.categoryId === categoryId))?.id ?? null;
}

function currentActiveCategory() {
  const threshold = Math.min(320, Math.max(140, window.innerHeight * 0.32));
  let activeId = DOCUMENTATION_CATEGORIES[0]?.id ?? "overview";

  for (const category of DOCUMENTATION_CATEGORIES) {
    const element = document.getElementById(category.id);

    if (!element) {
      continue;
    }

    const top = element.getBoundingClientRect().top;

    if (top <= threshold) {
      activeId = category.id;
      continue;
    }

    break;
  }

  return activeId;
}
