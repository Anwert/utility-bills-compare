"use strict";

/* The «Источники» tab. Draws data/methodology.json (ubc/methodology.py); no facts live here. */
(function (root) {
  var HASH = "#sources";
  var LINK = /https?:\/\/[^\s<>"«»]+/g;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function isOpen(hash) {
    return hash === HASH;
  }

  function anchor(url, text) {
    if (!/^https?:\/\//.test(String(url || ""))) return escapeHtml(text);
    return (
      '<a href="' +
      escapeHtml(url) +
      '" target="_blank" rel="noopener noreferrer">' +
      escapeHtml(text) +
      "</a>"
    );
  }

  /* A bracket belongs to the address only when the address opened it. */
  function trimUrl(url) {
    var cut = url;
    for (;;) {
      var last = cut.charAt(cut.length - 1);
      var unopened = last === ")" && cut.split("(").length < cut.split(")").length;
      if (!unopened && ".,;:!?".indexOf(last) === -1) return cut;
      cut = cut.slice(0, -1);
    }
  }

  function prose(value) {
    var text = String(value == null ? "" : value);
    var html = "";
    var from = 0;
    var match;
    LINK.lastIndex = 0;
    while ((match = LINK.exec(text)) !== null) {
      var url = trimUrl(match[0]);
      html += escapeHtml(text.slice(from, match.index)) + anchor(url, url);
      from = match.index + url.length;
      LINK.lastIndex = from;
    }
    return html + escapeHtml(text.slice(from));
  }

  function list(items, draw) {
    if (!items || !items.length) return "";
    var html = '<ul class="src-list">';
    for (var i = 0; i < items.length; i += 1) html += draw(items[i]);
    return html + "</ul>";
  }

  function textItem(text) {
    return "<li>" + prose(text) + "</li>";
  }

  function details(title, body) {
    if (!body) return "";
    return "<details><summary>" + escapeHtml(title) + "</summary>" + body + "</details>";
  }

  function sourceItem(source) {
    var facts = escapeHtml(source.kind);
    if (source.reading) facts += " · " + escapeHtml(source.reading);
    return (
      '<li class="' +
      (source.enabled ? "src" : "src off") +
      '">' +
      anchor(source.url, source.title) +
      "<small>" +
      facts +
      "</small></li>"
    );
  }

  function block(title, body) {
    return body ? '<h3 class="src-h">' + escapeHtml(title) + "</h3>" + body : "";
  }

  function article(item) {
    return (
      '<div class="src-article"><b>' +
      escapeHtml(item.label) +
      "</b>" +
      list(item.sources, sourceItem) +
      (item.note ? "<p>" + prose(item.note) + "</p>" : "") +
      "</div>"
    );
  }

  function articles(city) {
    if (city.notice) return '<p class="src-notice">' + prose(city.notice) + "</p>";
    var html = "";
    for (var i = 0; i < city.articles.length; i += 1) html += article(city.articles[i]);
    return block("Тарифы", html);
  }

  function reasons(group) {
    return "<li><b>" + escapeHtml(group.label) + "</b>" + list(group.reasons, textItem) + "</li>";
  }

  function houses(city) {
    var info = city.houses;
    var html = info.sites.length
      ? list(info.sites, sourceItem)
      : "<p>Сайты объявлений не подключены.</p>";
    if (info.class_note) html += "<p>" + prose(info.class_note) + "</p>";
    html += details("Правила: какой дом к какому классу относится", list(info.classes, reasons));
    html += details(
      "Правила: как определяется отопление",
      list(info.heatings, reasons) +
        list(info.unavailable, function (row) {
          return "<li><b>" + escapeHtml(row.label) + "</b>: " + prose(row.text) + "</li>";
        })
    );
    html += details(
      "Средние цены по районам",
      list(info.prices, function (row) {
        return (
          "<li><b>" +
          escapeHtml(row.districts) +
          "</b> — " +
          escapeHtml(row.price) +
          "<small>" +
          prose(row.source) +
          "</small></li>"
        );
      })
    );
    html += details("Открытые данные о домах", list(info.open_data, textItem));
    return block("Дома", html);
  }

  function norms(city) {
    var html = "";
    for (var i = 0; i < city.norms.length; i += 1) {
      var group = city.norms[i];
      html +=
        '<div class="src-article"><b>' +
        escapeHtml(group.label) +
        (group.scope ? " · " + escapeHtml(group.scope) : "") +
        "</b>" +
        list(group.rows, function (row) {
          return (
            "<li>" +
            escapeHtml(row.classes) +
            ": <b>" +
            escapeHtml(row.value) +
            "</b><small>" +
            escapeHtml(row.flat) +
            "</small>" +
            details(
              "Откуда норма",
              "<p>" + prose(row.source) + "</p>" + (row.note ? "<p>" + prose(row.note) + "</p>" : "")
            ) +
            "</li>"
          );
        }) +
        "</div>";
    }
    return block("Нормы расхода", html);
  }

  function cityHtml(city) {
    return (
      '<h2 class="src-title">Откуда данные: ' +
      escapeHtml(city.name) +
      "</h2>" +
      articles(city) +
      block(
        "Курс валюты",
        list(city.exchange.sources, sourceItem) +
          (city.exchange.note ? "<p>" + prose(city.exchange.note) + "</p>" : "")
      ) +
      block("Погода", list(city.weather, sourceItem)) +
      houses(city) +
      norms(city)
    );
  }

  function marksHtml(marks) {
    return details(
      "Что значат пометки у сумм",
      list(marks, function (mark) {
        return (
          '<li><span class="mark">' +
          escapeHtml(mark.label) +
          "</span> " +
          prose(mark.text) +
          "</li>"
        );
      })
    );
  }

  function sectionHtml(section, city, document) {
    var html =
      '<h2 class="src-title">' +
      escapeHtml(section.title) +
      "</h2><p>" +
      prose(section.lead) +
      "</p>";
    var items = list(section.items, textItem);
    if (section.id === "calculation") return html + items + marksHtml(document.marks);
    if (section.id !== "limits") return html + details("Подробнее", items);
    return (
      html +
      items +
      (city && city.limits.length
        ? block("Отдельно про город " + city.name, list(city.limits, textItem))
        : "")
    );
  }

  function body(document, cityId) {
    var city = null;
    var html = "";
    var i;
    for (i = 0; i < document.cities.length; i += 1) {
      if (document.cities[i].id === cityId) city = document.cities[i];
    }
    html += city ? cityHtml(city) : "<p>Про этот город пока нет описания.</p>";
    for (i = 0; i < document.sections.length; i += 1) {
      html += sectionHtml(document.sections[i], city, document);
    }
    return (
      html +
      '<p class="src-repo">' +
      anchor(document.repo_url, "Код, данные и история тарифов — на GitHub") +
      "</p>"
    );
  }

  /* state: { methodology, error }; neither means the file is still on its way. */
  function render(state, cityId) {
    var html;
    if (state.methodology) html = body(state.methodology, cityId);
    else if (state.error) {
      html = '<p class="load-error" role="alert">' + escapeHtml(state.error) + "</p>";
    } else html = '<p class="loading" role="status">Загрузка…</p>';
    return '<main class="sources">' + html + "</main>";
  }

  function link(id, href, text, on) {
    return (
      '<a class="page-link" data-page="' +
      id +
      '" href="' +
      href +
      '"' +
      (on ? ' aria-current="page"' : "") +
      ">" +
      text +
      "</a>"
    );
  }

  function switcher(open) {
    return (
      '<nav class="pages" aria-label="Разделы">' +
      link("grid", "#", "Суммы", !open) +
      link("sources", HASH, "Источники и расчёт", open) +
      "</nav>"
    );
  }

  /* Resolves to a state for render(); never rejects. */
  function load(fetchImpl, prefix) {
    return Promise.resolve()
      .then(function () {
        return fetchImpl((prefix || "data/") + "methodology.json");
      })
      .then(function (response) {
        if (!response || !response.ok) throw new Error("no methodology.json");
        return response.json();
      })
      .then(
        function (document) {
          return { methodology: document };
        },
        function () {
          return {
            error:
              "Не удалось загрузить описание источников. " +
              "На своём компьютере сначала выполните python -m ubc.methodology.",
          };
        }
      );
  }

  root.UbcSources = {
    isOpen: isOpen,
    load: load,
    render: render,
    switcher: switcher,
  };
})(globalThis);
