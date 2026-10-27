/**
 * Генератор синтетических ответов для Google Form (Apps Script).
 *
 * Установка: откройте форму в режиме редактирования → ⋮ → «Редактор скриптов»,
 * вставьте этот файл целиком, сохраните.
 *
 *   previewOne()  — сгенерировать один ответ и только вывести его в журнал (ничего не отправляет)
 *   submitOne()   — сгенерировать и отправить один ответ
 *   dumpStructure() — вывести структуру формы в JSON (пришлите её для тонкой настройки)
 *
 * Без ручных правил скрипт работает «универсально»:
 *  - у каждого респондента есть скрытая черта z и стиль ответа (согласие, крайности, середина, небрежность),
 *    поэтому шкалы и сетки внутри одного ответа согласованы, а между людьми — различаются;
 *  - у каждого варианта ответа есть стабильная «популярность» (зависит от id вопроса),
 *    поэтому распределения по выборке получаются неравномерными, как в реальности;
 *  - соблюдаются переходы между разделами (ветвление по ответу и «после раздела перейти к…»).
 */

// ---------- случайность ----------

function randn() {
  return Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random());
}

// Детерминированный генератор: одинаковый seed → одинаковая последовательность.
function seededRng(seed) {
  let h = 2166136261;
  for (const ch of String(seed)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return function () {
    h = (h + 0x6d2b79f5) | 0;
    let t = Math.imul(h ^ (h >>> 15), 1 | h);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp(x, lo, hi) {
  return Math.max(lo, Math.min(hi, x));
}

// Стабильные «популярности» вариантов для конкретного вопроса.
function choiceWeights(seed, n) {
  const r = seededRng(seed);
  return Array.from({ length: n }, () => Math.exp(0.9 * Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r())));
}

function pickIndex(weights) {
  let x = Math.random() * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < weights.length; i++) if ((x -= weights[i]) <= 0) return i;
  return weights.length - 1;
}

// ---------- персона ----------

function makePersona() {
  const careless = Math.random() < 0.04;
  return {
    z: randn(),                         // общая скрытая установка (условно «удовлетворённость»)
    acquiescence: randn() * 0.3,        // склонность соглашаться
    extreme: Math.random() < 0.15,      // выбирает крайние значения
    midpoint: Math.random() < 0.1,      // тяготеет к середине
    careless: careless,                 // отвечает «прямой линией»
    flat: randn() * 0.5,                // её значение для небрежного
    skipRate: Math.random() * 0.15,     // склонность пропускать необязательное
  };
}

// Порядковый ответ на шкале [min, max] с учётом персоны и особенностей вопроса.
function ordinal(p, min, max, seed) {
  if (p.careless) return Math.round(clamp((min + max) / 2 + p.flat * (max - min) / 4, min, max));
  const r = seededRng(seed);
  const loading = 0.4 + 0.5 * r();       // насколько вопрос связан с z
  const bias = (r() - 0.5) * 0.8;        // общий сдвиг вопроса
  let x = loading * p.z + p.acquiescence + bias + 0.6 * randn();
  if (p.extreme) x *= 1.8;
  if (p.midpoint) x *= 0.4;
  return Math.round(clamp((min + max) / 2 + x * (max - min) / 4, min, max));
}

// ---------- ответ на один вопрос ----------

// Возвращает { response, shown, nav } или null, если вопрос пропущен.
function answerItem(item, p) {
  const T = FormApp.ItemType;
  const type = item.getType();
  const id = item.getId();
  const title = item.getTitle();

  switch (type) {
    case T.MULTIPLE_CHOICE:
    case T.LIST: {
      const it = type === T.LIST ? item.asListItem() : item.asMultipleChoiceItem();
      if (!it.isRequired() && Math.random() < p.skipRate / 3) return null;
      const choices = it.getChoices();
      const values = choices.map(c => c.getValue());
      const idx = looksOrdinal(values)
        ? ordinal(p, 0, choices.length - 1, id)
        : pickIndex(choiceWeights(id, choices.length));
      const c = choices[idx];
      return { response: it.createResponse(c.getValue()), shown: c.getValue(), nav: choiceNav(c) };
    }
    case T.CHECKBOX: {
      const it = item.asCheckboxItem();
      const choices = it.getChoices().map(c => c.getValue());
      const w = choiceWeights(id, choices.length);
      const max = Math.max.apply(null, w);
      let sel = choices.filter((_, i) => Math.random() < 0.15 + 0.5 * w[i] / max);
      if (!sel.length) {
        if (!it.isRequired()) return null;
        sel = [choices[pickIndex(w)]];
      }
      return { response: it.createResponse(sel), shown: sel.join('; ') };
    }
    case T.SCALE: {
      const it = item.asScaleItem();
      const v = ordinal(p, it.getLowerBound(), it.getUpperBound(), id);
      return { response: it.createResponse(v), shown: v };
    }
    case T.RATING: {
      const it = item.asRatingItem();
      const v = ordinal(p, 1, it.getRatingScaleLevel(), id);
      return { response: it.createResponse(v), shown: v };
    }
    case T.GRID: {
      const it = item.asGridItem();
      const cols = it.getColumns();
      const vals = it.getRows().map((row, i) => cols[ordinal(p, 0, cols.length - 1, id + ':' + i)]);
      return { response: it.createResponse(vals), shown: vals.join(' | ') };
    }
    case T.CHECKBOX_GRID: {
      const it = item.asCheckboxGridItem();
      const cols = it.getColumns();
      const vals = it.getRows().map((row, i) => {
        const w = choiceWeights(id + ':' + i, cols.length);
        return [cols[pickIndex(w)]];
      });
      return { response: it.createResponse(vals), shown: vals.map(v => v.join(',')).join(' | ') };
    }
    case T.TEXT: {
      const it = item.asTextItem();
      let v = null;
      if (/вік|возраст|скільки.*рок|сколько.*лет|age/i.test(title)) v = String(18 + Math.floor(Math.random() * 48));
      else if (it.isRequired()) v = '—';
      if (v === null) return null;
      return { response: it.createResponse(v), shown: v };
    }
    case T.PARAGRAPH_TEXT: {
      const it = item.asParagraphTextItem();
      if (!it.isRequired()) return null;
      return { response: it.createResponse('—'), shown: '—' };
    }
    case T.DATE: {
      const it = item.asDateItem();
      const d = new Date(Date.now() - Math.floor(Math.random() * 365) * 864e5);
      return { response: it.createResponse(d), shown: d.toDateString() };
    }
    case T.DATETIME: {
      const it = item.asDateTimeItem();
      const d = new Date(Date.now() - Math.floor(Math.random() * 365 * 864e5));
      return { response: it.createResponse(d), shown: d.toString() };
    }
    case T.TIME: {
      const it = item.asTimeItem();
      const h = 8 + Math.floor(Math.random() * 12), m = Math.floor(Math.random() * 60);
      return { response: it.createResponse(h, m), shown: h + ':' + m };
    }
    case T.DURATION: {
      const it = item.asDurationItem();
      const h = Math.floor(Math.random() * 3), m = Math.floor(Math.random() * 60);
      return { response: it.createResponse(h, m, 0), shown: h + 'h ' + m + 'm' };
    }
    default:
      return null; // заголовки, картинки, видео и т. п.
  }
}

// Варианты похожи на шкалу (Лайкерт, частота, баллы)? Тогда отвечаем через скрытую черту, а не случайно.
const ORDINAL_RE = /^\s*[-+]?\d|згод|соглас|завжди|ніколи|часто|рідко|іноді|інколи|всегда|никогда|редко|иногда|скоріше|скорее|повністю|полностью|зовсім|совсем|майже|почти|дуже|очень|так\b|ні\b|да\b|нет\b|не знаю|важко сказати/i;

function looksOrdinal(values) {
  return values.length >= 2 && values.every(v => ORDINAL_RE.test(v));
}

function choiceNav(choice) {
  try {
    const t = choice.getPageNavigationType();
    if (t === FormApp.PageNavigationType.SUBMIT) return { submit: true };
    if (t === FormApp.PageNavigationType.GO_TO_PAGE && choice.getGotoPage()) return { goTo: choice.getGotoPage().getId() };
  } catch (e) {}
  return null;
}

function sectionNav(pageBreak) {
  const t = pageBreak.getPageNavigationType();
  if (t === FormApp.PageNavigationType.SUBMIT) return { submit: true };
  if (t === FormApp.PageNavigationType.GO_TO_PAGE && pageBreak.getGoToPage()) return { goTo: pageBreak.getGoToPage().getId() };
  return null;
}

// ---------- сборка ответа с учётом переходов между разделами ----------

function buildResponse(form, p) {
  const items = form.getItems();
  const indexById = {};
  items.forEach((it, i) => (indexById[it.getId()] = i));

  const resp = form.createResponse();
  const log = [];
  let pendingNav = null; // переход, выбранный ответом внутри текущего раздела
  let skipTo = null;     // id раздела, к которому прыгаем

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const isBreak = item.getType() === FormApp.ItemType.PAGE_BREAK;

    if (skipTo !== null) {
      if (isBreak && item.getId() === skipTo) {
        skipTo = null;
        pendingNav = null;
        log.push('=== ' + item.getTitle());
      }
      continue;
    }

    if (isBreak) {
      // Конец предыдущего раздела: решаем, куда идти дальше.
      const nav = pendingNav || sectionNav(item.asPageBreakItem());
      pendingNav = null;
      if (nav && nav.submit) break;
      if (nav && nav.goTo !== undefined && nav.goTo !== item.getId() && indexById[nav.goTo] > i) {
        skipTo = nav.goTo;
        continue;
      }
      log.push('=== ' + item.getTitle());
      continue;
    }

    const a = answerItem(item, p);
    if (!a) {
      if (item.getType() !== FormApp.ItemType.SECTION_HEADER) log.push('  [пропуск] ' + item.getTitle());
      continue;
    }
    resp.withItemResponse(a.response);
    if (a.nav) pendingNav = a.nav;
    log.push('  ' + item.getTitle() + ' → ' + a.shown);
  }
  return { resp: resp, log: log };
}

// ---------- точки входа ----------

function previewOne() {
  const p = makePersona();
  const { log } = buildResponse(FormApp.getActiveForm(), p);
  Logger.log('Персона: ' + JSON.stringify(p) + '\n' + log.join('\n'));
}

function submitOne() {
  const p = makePersona();
  const { resp, log } = buildResponse(FormApp.getActiveForm(), p);
  resp.submit();
  Logger.log('ОТПРАВЛЕНО. Персона: ' + JSON.stringify(p) + '\n' + log.join('\n'));
}

function dumpStructure() {
  const T = FormApp.ItemType;
  const out = FormApp.getActiveForm().getItems().map(it => {
    const t = it.getType();
    const o = { id: it.getId(), type: String(t), title: it.getTitle() };
    try {
      if (t === T.MULTIPLE_CHOICE) o.choices = it.asMultipleChoiceItem().getChoices().map(c => c.getValue());
      if (t === T.CHECKBOX) o.choices = it.asCheckboxItem().getChoices().map(c => c.getValue());
      if (t === T.LIST) o.choices = it.asListItem().getChoices().map(c => c.getValue());
      if (t === T.SCALE) { const s = it.asScaleItem(); o.min = s.getLowerBound(); o.max = s.getUpperBound(); }
      if (t === T.GRID) { const g = it.asGridItem(); o.rows = g.getRows(); o.cols = g.getColumns(); }
      if (t === T.CHECKBOX_GRID) { const g = it.asCheckboxGridItem(); o.rows = g.getRows(); o.cols = g.getColumns(); }
    } catch (e) {}
    return o;
  });
  Logger.log(JSON.stringify(out));
}
