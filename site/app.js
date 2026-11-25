"use strict";

/* CityMonth page. No build step and no Python import: labels live here. */
(function (root) {
  var CITIES = [
    ["yerevan", "Ереван"],
    ["tbilisi", "Тбилиси"],
    ["belgrade", "Белград"],
    ["warsaw", "Варшава"],
    ["lisbon", "Лиссабон"],
    ["berlin", "Берлин"],
  ];
  var CLASSES = [
    ["soviet_panel", "Советский / панельный фонд"],
    ["typical_newbuild", "Типовая новостройка 2000–2010-х"],
    ["business", "Бизнес-класс"],
  ];
  var HEATS = [
    ["district", "Центральное"],
    ["gas_boiler", "Индивидуальный газовый котёл"],
    ["electric", "Электрическое"],
  ];
  var ITEMS = {
    maintenance: "Обслуживание дома",
    heating: "Отопление",
    electricity: "Электричество",
    gas: "Газ",
    water: "Вода",
    hot_water: "Горячая вода",
    sewage: "Канализация",
    waste: "Мусор",
  };
  var MARKS = {
    fixed: "фиксированный платёж",
    norm_based: "по норме расхода",
    statistics_estimate: "оценка по статистике",
    third_party: "сторонний сайт",
    llm_extracted: "извлечено моделью",
    stale: "значение не обновилось",
  };
  var MARK_ORDER = [
    "fixed",
    "norm_based",
    "statistics_estimate",
    "third_party",
    "llm_extracted",
    "stale",
  ];
  var PAID_MARKS = {
    statistics_estimate: true,
    third_party: true,
    llm_extracted: true,
  };
  /* ``field`` is the rate in data/display-rates.json. Euro is what the data is in. */
  var CURRENCIES = [
    { code: "EUR", sign: "€", name: "Евро", legend: "в евро", of: "евро", field: null },
    { code: "AMD", sign: "֏", name: "Драмы", legend: "в драмах", of: "драма", field: "amd_per_eur" },
    { code: "RUB", sign: "₽", name: "Рубли", legend: "в рублях", of: "рубля", field: "rub_per_eur" },
  ];
  var MONTHS = [
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatPlain(value, maxDecimals) {
    var number = Number(value);
    if (!isFinite(number)) return "";
    var factor = Math.pow(10, maxDecimals);
    var rounded = Math.round(number * factor) / factor;
    var text = rounded.toFixed(maxDecimals);
    if (text.indexOf(".") !== -1) {
      text = text.replace(/0+$/, "").replace(/\.$/, "");
    }
    return text.replace(".", ",");
  }

  function formatEur(value) {
    var number = Number(value);
    if (!isFinite(number)) return "";
    return "€" + (Math.round(number * 100) / 100).toFixed(2).replace(".", ",");
  }

  function currencyOf(view) {
    for (var i = 0; i < CURRENCIES.length; i += 1) {
      if (CURRENCIES[i].code === view.currency) return CURRENCIES[i];
    }
    return CURRENCIES[0];
  }

  /* The rate of the shown month, or null: a neighbouring month's rate is not a substitute. */
  function rateOf(view, currency) {
    var list = view.rates && Array.isArray(view.rates.rates) ? view.rates.rates : [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].month !== view.month) continue;
      var value = Number(list[i][currency.field]);
      return value > 0 && isFinite(value) ? { value: value, rate: list[i] } : null;
    }
    return null;
  }

  function grouped(whole) {
    return String(whole).replace(/\B(?=(\d{3})+$)/g, " ");
  }

  /* Amounts of one drawing. Other currencies are whole units, and every sum adds up
     the rounded lines, so a column of figures always matches its total. */
  function moneyOf(view) {
    var currency = currencyOf(view);
    var found = currency.field ? rateOf(view, currency) : null;
    if (!found) {
      return {
        currency: CURRENCIES[0],
        wanted: currency,
        missing: Boolean(currency.field),
        line: function (line) {
          return formatEur(line.amount_eur);
        },
        sum: function (lines, eur) {
          return formatEur(eur);
        },
      };
    }
    var convert = function (eur) {
      return Math.round(Number(eur) * found.value);
    };
    var show = function (whole) {
      return isFinite(whole) ? grouped(whole) + " " + currency.sign : "";
    };
    return {
      currency: currency,
      wanted: currency,
      missing: false,
      found: found,
      line: function (line) {
        return show(convert(line.amount_eur));
      },
      sum: function (lines) {
        var total = 0;
        for (var i = 0; i < lines.length; i += 1) total += convert(lines[i].amount_eur);
        return show(total);
      },
    };
  }

  function formatCount(value) {
    var number = Number(value);
    if (!isFinite(number)) return "";
    if (Math.abs(number - Math.round(number)) < 1e-9) return String(Math.round(number));
    return formatPlain(number, 2);
  }

  function formatMonth(yearMonth) {
    var match = /^(\d{4})-(\d{2})$/.exec(String(yearMonth));
    if (!match) return String(yearMonth);
    var name = MONTHS[Number(match[2]) - 1];
    if (!name) return String(yearMonth);
    return name.charAt(0).toUpperCase() + name.slice(1) + " " + match[1];
  }

  function formatDate(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
    if (!match) return String(value);
    return match[3] + "." + match[2] + "." + match[1];
  }

  function labelOf(pairs, code) {
    for (var i = 0; i < pairs.length; i += 1) {
      if (pairs[i][0] === code) return pairs[i][1];
    }
    return code;
  }

  function known(pairs, code) {
    for (var i = 0; i < pairs.length; i += 1) {
      if (pairs[i][0] === code) return true;
    }
    return false;
  }

  function indexCells(snapshot) {
    var map = {};
    var cells = snapshot && snapshot.cells ? snapshot.cells : [];
    for (var i = 0; i < cells.length; i += 1) {
      var cell = cells[i];
      map[cell.house_class + ":" + cell.heating] = cell;
    }
    return map;
  }

  function defaultCell(snapshot) {
    var map = indexCells(snapshot);
    var house;
    var heat;
    for (house = 0; house < CLASSES.length; house += 1) {
      for (heat = 0; heat < HEATS.length; heat += 1) {
        var cell = map[CLASSES[house][0] + ":" + HEATS[heat][0]];
        if (cell && cell.status === "ok") return cell;
      }
    }
    for (house = 0; house < CLASSES.length; house += 1) {
      for (heat = 0; heat < HEATS.length; heat += 1) {
        var fallback = map[CLASSES[house][0] + ":" + HEATS[heat][0]];
        if (fallback) return fallback;
      }
    }
    return null;
  }

  function monthsFor(snapshots, city) {
    var byMonth = {};
    var order = [];
    for (var i = 0; i < snapshots.length; i += 1) {
      var snap = snapshots[i];
      if (!snap || snap.city !== city || typeof snap.month !== "string") continue;
      if (!Object.prototype.hasOwnProperty.call(byMonth, snap.month)) order.push(snap.month);
      byMonth[snap.month] = snap;
    }
    order.sort();
    var list = [];
    for (var m = 0; m < order.length; m += 1) list.push(byMonth[order[m]]);
    return list;
  }

  function viewModel(snapshots, overrides) {
    var list = Array.isArray(snapshots) ? snapshots : [];
    var options = overrides || {};
    var city = options.city;
    if (!known(CITIES, city)) {
      city = CITIES[0][0];
      for (var i = 0; i < CITIES.length; i += 1) {
        if (monthsFor(list, CITIES[i][0]).length) {
          city = CITIES[i][0];
          break;
        }
      }
    }
    var months = monthsFor(list, city);
    var month = options.month;
    var found = false;
    for (var m = 0; m < months.length; m += 1) {
      if (months[m].month === month) found = true;
    }
    if (!found) month = months.length ? months[months.length - 1].month : null;
    var snapshot = null;
    for (var s = 0; s < months.length; s += 1) {
      if (months[s].month === month) snapshot = months[s];
    }
    var houseClass = options.houseClass;
    var heating = options.heating;
    if (!snapshot || !known(CLASSES, houseClass) || !known(HEATS, heating)) {
      var picked = snapshot ? defaultCell(snapshot) : null;
      houseClass = picked ? picked.house_class : null;
      heating = picked ? picked.heating : null;
    }
    return {
      any: list.length > 0,
      reference: list.length ? list[0].apartment : null,
      city: city,
      month: month,
      months: months,
      snapshot: snapshot,
      houseClass: houseClass,
      heating: heating,
      currency: options.currency,
      rates: options.rates,
    };
  }

  function currencySwitch(money) {
    var html = '<div class="currencies" role="group" aria-label="Валюта">';
    for (var i = 0; i < CURRENCIES.length; i += 1) {
      var one = CURRENCIES[i];
      var on = one.code === money.wanted.code;
      html +=
        '<button type="button" class="' +
        (on ? "currency on" : "currency") +
        '" data-currency="' +
        one.code +
        '" aria-pressed="' +
        (on ? "true" : "false") +
        '">' +
        one.sign +
        " " +
        one.name +
        "</button>";
    }
    html += "</div>";
    if (money.missing) {
      html +=
        '<p class="rate-missing">Курса ' +
        money.wanted.of +
        " за этот месяц нет — суммы показаны в евро.</p>";
    }
    return html;
  }

  function tabs(view) {
    var html = '<div class="tabs" role="tablist">';
    for (var i = 0; i < CITIES.length; i += 1) {
      var id = CITIES[i][0];
      var on = id === view.city;
      html +=
        '<button type="button" class="' +
        (on ? "tab on" : "tab") +
        '" data-city="' +
        id +
        '" aria-selected="' +
        (on ? "true" : "false") +
        '">' +
        escapeHtml(CITIES[i][1]) +
        "</button>";
    }
    html += "</div>";
    return html;
  }

  function apartmentLine(apartment) {
    if (!apartment) return "Эталонная квартира: 2 комнаты, 60 м², 2 жильца.";
    return (
      formatCount(apartment.rooms) +
      " комнаты, " +
      formatCount(apartment.area_m2) +
      " м², " +
      formatCount(apartment.occupants) +
      " жильца"
    );
  }

  function sourceAnchor(url, text) {
    return '<a href="' + escapeHtml(url) + '">' + escapeHtml(text) + "</a>";
  }

  function hostOf(url) {
    try {
      return new URL(url).host || "источник";
    } catch (err) {
      return "источник";
    }
  }

  function marksHtml(marks) {
    var present = {};
    var list = marks || [];
    for (var i = 0; i < list.length; i += 1) present[list[i]] = true;
    var html = "";
    for (var m = 0; m < MARK_ORDER.length; m += 1) {
      var code = MARK_ORDER[m];
      if (!present[code]) continue;
      html += '<span class="mark">' + escapeHtml(MARKS[code] || code) + "</span>";
    }
    return html;
  }

  function hasPaidMark(marks) {
    var list = marks || [];
    for (var i = 0; i < list.length; i += 1) {
      if (PAID_MARKS[list[i]]) return true;
    }
    return false;
  }

  function lineHtml(line, paidNote, money) {
    var bits =
      "тариф с " +
      escapeHtml(formatDate(line.valid_from)) +
      " · " +
      sourceAnchor(line.source_url, hostOf(line.source_url));
    var note = line.note ? '<small class="note">' + escapeHtml(line.note) + "</small>" : "";
    var repeated = String(line.note || "").indexOf(paidNote) !== -1;
    var paid = hasPaidMark(line.marks) && !repeated
      ? '<p class="paid">' + escapeHtml(paidNote) + "</p>"
      : "";
    return (
      '<div class="li"><div><span class="item">' +
      escapeHtml(ITEMS[line.item] || line.item) +
      "</span><small>" +
      bits +
      "</small>" +
      marksHtml(line.marks) +
      note +
      paid +
      '</div><b>' +
      money.line(line) +
      "</b></div>"
    );
  }

  function sectionHtml(title, lines, subtotalLabel, subtotal, paidNote, money) {
    var html = '<h3 class="sec">' + title + "</h3>";
    for (var i = 0; i < lines.length; i += 1) html += lineHtml(lines[i], paidNote, money);
    html +=
      '<div class="sum"><span>' +
      subtotalLabel +
      "</span><span>" +
      money.sum(lines, subtotal) +
      "</span></div>";
    return html;
  }

  function linesFor(cell, category) {
    var found = [];
    var lines = cell.lines || [];
    for (var i = 0; i < lines.length; i += 1) {
      if (lines[i].category === category) found.push(lines[i]);
    }
    return found;
  }

  function buildingHtml(building) {
    if (!building) return "";
    if (building.url && building.address) {
      return "<p>" + sourceAnchor(building.url, building.address) + "</p>";
    }
    if (building.url) return "<p>" + sourceAnchor(building.url, "объявление") + "</p>";
    return "<p>" + escapeHtml(building.address || "") + "</p>";
  }

  function rateHtml(snapshot, money) {
    var rate = snapshot.exchange_rate;
    var shown = "";
    if (money.found && money.currency.code !== rate.currency) {
      shown =
        "<p>" +
        formatPlain(money.found.value, 4) +
        " " +
        money.currency.code +
        " за 1 €, " +
        escapeHtml(formatMonth(money.found.rate.month).toLowerCase()) +
        ", источник " +
        sourceAnchor(money.found.rate.source_url, money.found.rate.source_name) +
        "</p>";
    }
    return (
      "<p>" +
      formatPlain(rate.per_eur, 4) +
      " " +
      escapeHtml(rate.currency) +
      " за 1 €, " +
      escapeHtml(formatMonth(rate.month).toLowerCase()) +
      ", источник " +
      sourceAnchor(rate.source_url, rate.source_name) +
      "</p>" +
      shown
    );
  }

  function sheet(view, money) {
    var snapshot = view.snapshot;
    var map = indexCells(snapshot);
    var key = view.houseClass + ":" + view.heating;
    var cell = map[key];
    var title =
      "<b>" +
      escapeHtml(labelOf(CITIES, snapshot.city)) +
      "</b> · " +
      escapeHtml(labelOf(CLASSES, view.houseClass)) +
      " · " +
      escapeHtml(labelOf(HEATS, view.heating));
    if (!cell || cell.status !== "ok") {
      var reason = cell && cell.empty_reason ? cell.empty_reason : "в файле месяца нет этой ячейки";
      return (
        '<section class="sheet"><div class="sh">' +
        title +
        '</div><p class="empty-reason">' +
        escapeHtml(reason) +
        "</p></section>"
      );
    }
    var housing = linesFor(cell, "housing");
    var utility = linesFor(cell, "utility");
    return (
      '<section class="sheet"><div class="sh">' +
      title +
      buildingHtml(cell.building) +
      rateHtml(snapshot, money) +
      "</div>" +
      sectionHtml(
        "Жилищные услуги",
        housing,
        "Итого жилищные",
        cell.housing_eur,
        snapshot.paid_data_note,
        money
      ) +
      sectionHtml(
        "Коммунальные услуги",
        utility,
        "Итого коммунальные",
        cell.utility_eur,
        snapshot.paid_data_note,
        money
      ) +
      '<div class="sum grand"><span>Итого</span><span>' +
      money.sum(cell.lines, cell.total_eur) +
      "</span></div></section>"
    );
  }

  function grid(view, money) {
    var map = indexCells(view.snapshot);
    var html = '<div class="grid"><div class="corner"></div>';
    var heat;
    var house;
    for (heat = 0; heat < HEATS.length; heat += 1) {
      html += '<div class="hd">' + escapeHtml(HEATS[heat][1]) + "</div>";
    }
    for (house = 0; house < CLASSES.length; house += 1) {
      html += '<div class="rh">' + escapeHtml(CLASSES[house][1]) + "</div>";
      for (heat = 0; heat < HEATS.length; heat += 1) {
        var houseClass = CLASSES[house][0];
        var heating = HEATS[heat][0];
        var cell = map[houseClass + ":" + heating];
        var selected = houseClass === view.houseClass && heating === view.heating;
        var empty = !cell || cell.status !== "ok";
        var cls = "cell" + (empty ? " empty" : "") + (selected ? " selected" : "");
        var body;
        if (empty) {
          var reason = cell && cell.empty_reason ? cell.empty_reason : "в файле месяца нет этой ячейки";
          body = "<b>—</b><span class=\"reason\">" + escapeHtml(reason) + "</span>";
        } else {
          body =
            "<b>" +
            money.sum(cell.lines, cell.total_eur) +
            '</b><span class="parts">жил. ' +
            money.sum(linesFor(cell, "housing"), cell.housing_eur) +
            "<br>ком. " +
            money.sum(linesFor(cell, "utility"), cell.utility_eur) +
            "</span>";
        }
        html +=
          '<button type="button" class="' +
          cls +
          '" data-cell="' +
          houseClass +
          ":" +
          heating +
          '">' +
          body +
          "</button>";
      }
    }
    html += "</div>";
    return html;
  }

  function render(snapshots, overrides) {
    var view = viewModel(snapshots, overrides);
    var html = '<div class="page">';
    var sources = root.UbcSources;
    var sourcesState = sources && overrides ? overrides.sources : null;
    if (view.snapshot && view.snapshot.example && !sourcesState) {
      html += '<p class="example-banner">ПРИМЕР ДАННЫХ — цифры выдуманы</p>';
    }
    html += "<h1>Коммуналка в реальных домах</h1>";
    if (sources) html += sources.switcher(Boolean(sourcesState));
    html += tabs(view);
    html += '<div class="wrap">';
    if (sourcesState) return html + sources.render(sourcesState, view.city) + "</div></div>";
    if (!view.any) {
      html += '<p class="nodata">Нет посчитанных месяцев.</p>';
      html += '<p class="meta">Эталонная квартира: 2 комнаты, 60 м², 2 жильца.</p>';
      html += "</div></div>";
      return html;
    }
    var meta = apartmentLine(view.snapshot ? view.snapshot.apartment : view.reference);
    if (view.snapshot) meta += ". Обновлено " + formatDate(view.snapshot.updated_at);
    html += '<p class="meta">' + meta + "</p>";
    if (!view.snapshot) {
      html += '<p class="nodata">За этот город ещё нет посчитанного месяца.</p>';
      html += "</div></div>";
      return html;
    }
    html += '<label class="month-label" for="month">Месяц</label>';
    html += '<select id="month" data-month>';
    for (var i = 0; i < view.months.length; i += 1) {
      var snap = view.months[i];
      html +=
        '<option value="' +
        escapeHtml(snap.month) +
        '"' +
        (snap.month === view.month ? " selected" : "") +
        ">" +
        escapeHtml(formatMonth(snap.month)) +
        "</option>";
    }
    html += "</select>";
    var money = moneyOf(view);
    html += currencySwitch(money);
    html +=
      '<p class="legend">Итого ' +
      money.currency.legend +
      " за месяц для эталонной квартиры. Нажмите на ячейку — внизу разбивка.</p>";
    html += grid(view, money);
    html += sheet(view, money);
    html += "</div></div>";
    return html;
  }

  function safePath(rel) {
    if (typeof rel !== "string" || rel.length === 0) return false;
    if (rel.charAt(0) === "/" || rel.indexOf("\\") !== -1 || rel.indexOf("..") !== -1) return false;
    var parts = rel.split("/");
    for (var i = 0; i < parts.length; i += 1) {
      if (parts[i] === "" || parts[i] === ".") return false;
    }
    return true;
  }

  function loadIndex(fetchImpl, options) {
    var settings = options || {};
    var prefix = settings.prefix || "data/";
    var indexUrl = settings.indexUrl || prefix + "site-index.json";
    return Promise.resolve(fetchImpl(indexUrl)).then(function (indexResponse) {
      if (!indexResponse || !indexResponse.ok) {
        throw new Error("Не удалось прочитать список месяцев");
      }
      return indexResponse.json().then(function (index) {
        var files = index && index.files;
        if (!Array.isArray(files)) throw new Error("Не удалось прочитать список месяцев");
        var chain = Promise.resolve([]);
        files.forEach(function (file) {
          chain = chain.then(function (collected) {
            var rel = file && file.path;
            if (!safePath(rel)) throw new Error("Некорректный путь в списке месяцев");
            return Promise.resolve(fetchImpl(prefix + rel)).then(function (response) {
              if (!response || !response.ok) throw new Error("Не удалось прочитать " + rel);
              return response.json().then(function (document) {
                document.example = Boolean(file.example);
                collected.push(document);
                return collected;
              });
            });
          });
        });
        return chain;
      });
    });
  }

  /* Rates are optional: without them the page stays in euros. */
  function loadRates(fetchImpl, prefix) {
    return Promise.resolve()
      .then(function () {
        return fetchImpl((prefix || "data/") + "display-rates.json");
      })
      .then(function (response) {
        return response && response.ok ? response.json() : null;
      })
      .catch(function () {
        return null;
      });
  }

  function savedCurrency(host) {
    try {
      return host.localStorage.getItem("ubc-currency");
    } catch (err) {
      return null;
    }
  }

  function saveCurrency(host, code) {
    try {
      host.localStorage.setItem("ubc-currency", code);
    } catch (err) {
      /* The choice then lasts until the page is closed. */
    }
  }

  /* A redraw replaces every element: keep the keyboard focus and the scrolled city strip. */
  function redraw(container, html) {
    var active = root.document ? root.document.activeElement : null;
    var names = ["data-page", "data-city", "data-cell", "data-month", "data-currency"];
    var selector = null;
    for (var i = 0; active && container.contains(active) && i < names.length; i += 1) {
      if (active.hasAttribute(names[i])) {
        selector = "[" + names[i] + '="' + active.getAttribute(names[i]) + '"]';
      }
    }
    var strip = container.querySelector(".tabs");
    var left = strip ? strip.scrollLeft : 0;
    container.innerHTML = html;
    strip = container.querySelector(".tabs");
    if (strip) strip.scrollLeft = left;
    var again = selector ? container.querySelector(selector) : null;
    if (again) again.focus({ preventScroll: true });
  }

  function mount(container, options) {
    var settings = options || {};
    var fetchImpl = settings.fetch || function (url) {
      return root.fetch(url);
    };
    var selection = {};
    var host = settings.window || root;
    var sources = root.UbcSources;
    var sourcesState = null;
    var currency = savedCurrency(host);
    Promise.all([
      loadIndex(fetchImpl, { prefix: settings.prefix, indexUrl: settings.indexUrl }),
      loadRates(fetchImpl, settings.prefix),
    ])
      .then(function (loadedData) {
        var snapshots = loadedData[0];
        var draw = function () {
          var overrides = Object.assign({}, selection, {
            currency: currency,
            rates: loadedData[1],
          });
          if (sources && host.location && sources.isOpen(host.location.hash)) {
            if (!sourcesState) {
              sourcesState = {};
              sources.load(fetchImpl, settings.prefix).then(function (loaded) {
                sourcesState = loaded;
                draw();
              });
            }
            overrides.sources = sourcesState;
          }
          redraw(container, render(snapshots, overrides));
          var buttons = container.querySelectorAll("[data-city]");
          for (var i = 0; i < buttons.length; i += 1) {
            buttons[i].addEventListener("click", function (event) {
              selection = { city: event.currentTarget.getAttribute("data-city") };
              draw();
            });
          }
          var month = container.querySelector("[data-month]");
          if (month) {
            month.addEventListener("change", function () {
              selection = {
                city: selection.city,
                month: month.value,
                houseClass: selection.houseClass,
                heating: selection.heating,
              };
              draw();
            });
          }
          var currencies = container.querySelectorAll("[data-currency]");
          for (var k = 0; k < currencies.length; k += 1) {
            currencies[k].addEventListener("click", function (event) {
              currency = event.currentTarget.getAttribute("data-currency");
              saveCurrency(host, currency);
              draw();
            });
          }
          var cells = container.querySelectorAll("[data-cell]");
          for (var c = 0; c < cells.length; c += 1) {
            cells[c].addEventListener("click", function (event) {
              var parts = event.currentTarget.getAttribute("data-cell").split(":");
              selection = {
                city: selection.city,
                month: selection.month,
                houseClass: parts[0],
                heating: parts[1],
              };
              draw();
              var opened = container.querySelector(".sheet");
              if (opened && opened.scrollIntoView) {
                opened.scrollIntoView({ block: "nearest" });
              }
            });
          }
        };
        draw();
        if (sources && host.addEventListener) {
          host.addEventListener("hashchange", function () {
            draw();
            host.scrollTo(0, 0);
          });
        }
      })
      .catch(function (error) {
        var raw = error && error.message ? error.message : "";
        var message = raw;
        if (!raw || raw.indexOf("fetch") !== -1 || raw.indexOf("NetworkError") !== -1) {
          message =
            "Не удалось загрузить данные. Откройте страницу через локальный сервер, а не как файл.";
        }
        container.innerHTML = '<p class="load-error">' + escapeHtml(message) + "</p>";
      });
  }

  root.UbcSite = {
    render: render,
    loadIndex: loadIndex,
    mount: mount,
  };
})(globalThis);
