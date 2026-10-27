const $ = (id) => document.getElementById(id);
const names = {
  structure: "Құрылым",
  argumentation: "Аргументация",
  logic: "Логика",
  evidence: "Дәлелдер",
  language: "Тілдік сапа",
};
const elements = {
  introduction: "Кіріспе",
  thesis: "Тезис",
  arguments: "Аргументтер",
  evidence: "Дәлелдер",
  counterargument: "Қарсы пікір",
  conclusion: "Қорытынды",
};
let busy = false,
  historyOffset = 0,
  activeResult = null,
  parentId = null,
  submission = null;
let pollTimer = null,
  viewToken = 0,
  comparisonToken = 0,
  comparisonRevision = null;
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function button(text, action, className = "text-button") {
  const el = node("button", text, className);
  el.type = "button";
  el.onclick = action;
  return el;
}
function list(parent, title, items) {
  parent.append(node("p", title, "label"));
  const ul = node("ul");
  (items.length ? items : ["Анықталмады"]).forEach((text) =>
    ul.append(node("li", text)),
  );
  parent.append(ul);
}
function quotes(parent, items) {
  if (!items.length) parent.append(node("p", "Анықталмады", "muted"));
  items.forEach((text) => parent.append(node("blockquote", text)));
}
function error(message = "") {
  $("error").textContent = message;
  $("error").hidden = !message;
}
function setBusy(value, message = "") {
  busy = value;
  ["analyze", "reset", "image", "upload-image", "revise"].forEach(
    (id) => ($(id).disabled = value),
  );
  $("button-text").textContent = value ? "Өңделуде…" : "Эссені талдау";
  $("status").textContent = message;
  $("essay-form").setAttribute("aria-busy", String(value));
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-Requested-With": "tekSER", ...options.headers },
    signal: AbortSignal.timeout(190000),
  });
  if (response.status === 204) return null;
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("Серверден жауап алынбады. Кейінірек қайталаңыз.");
  }
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : data.detail?.message || "Сұрауды орындау мүмкін болмады.",
    );
  return data;
}
function hasPending(data) {
  return Object.values(data.sections || {}).some(
    (section) => section.status === "pending",
  );
}
function sectionFor(data, key) {
  return (
    data.sections?.[key] || {
      status:
        key === "grade"
          ? data.analysis
            ? "ready"
            : "not_run"
          : data.topic_alignment
            ? "ready"
            : "not_run",
    }
  );
}
function retryButton(key, section) {
  const el = button(
    "Тек осы бөлімді қайта тексеру",
    () => retry(key),
    "secondary retry-section",
  );
  el.dataset.retryAt = String(section.retry_at || 0);
  el.dataset.section = key;
  return el;
}
function updateRetryButtons() {
  document.querySelectorAll(".retry-section").forEach((el) => {
    const seconds = Math.max(
      0,
      Math.ceil(Number(el.dataset.retryAt) - Date.now() / 1000),
    );
    el.disabled = busy || seconds > 0;
    el.textContent = seconds
      ? `${seconds} секундтан кейін қайталау`
      : "Тек осы бөлімді қайта тексеру";
  });
}
setInterval(updateRetryButtons, 1000);

function showResult(data, scroll = true) {
  activeResult = data;
  sessionStorage.setItem("essay-result-id", String(data.id));
  $("results").hidden = false;
  $("results-title").textContent = data.title;
  $("total-score").textContent = data.total_score ?? "—";
  $("summary").textContent =
    data.analysis?.summary ||
    "Эссе сақталды. Бөлімдер дайын болған сайын осы жерде көрсетіледі.";
  const grade = sectionFor(data, "grade");
  $("grade-state").dataset.state = grade.status;
  $("grade-state").replaceChildren(node("h3", "Эссе сапасын бағалау"));
  if (grade.status === "ready")
    $("grade-state").append(
      node("p", "Дайын. Баға мен түсініктемелер сақталды."),
    );
  else if (grade.status === "pending")
    $("grade-state").append(
      node(
        "p",
        "Бағалау жүріп жатыр… Бұл беттен шықсаңыз да, сақталған эссені тарихтан аша аласыз.",
      ),
    );
  else
    $("grade-state").append(
      node(
        "p",
        grade.error || "Бағалау орындалмады. Жалған балл қойылған жоқ.",
      ),
      retryButton("grade", grade),
    );
  showTopicAlignment(data.topic_alignment, sectionFor(data, "topic"));
  $("grade-content").hidden = !data.analysis;
  if (data.analysis) showGrade(data);
  renderAnnotations(data);
  $("markers").replaceChildren();
  if (!data.markers?.length)
    $("markers").append(node("p", "Маркерлер анықталмады.", "muted"));
  (data.markers || []).forEach((item) =>
    $("markers").append(
      node("p", `${item.category}: «${item.text}»`),
      node("blockquote", item.sentence),
    ),
  );
  $("comparison-panel").hidden = !data.parent_id;
  if (data.parent_id && comparisonRevision !== `${data.id}:${data.revision}`) {
    comparisonRevision = `${data.id}:${data.revision}`;
    loadComparison(data);
  }
  updateRetryButtons();
  if (scroll) scrollToElement($("results"));
}
function scrollToElement(element) {
  element.scrollIntoView({
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
}
function showGrade(data) {
  const analysis = data.analysis;
  $("classification").replaceChildren(
    node(
      "span",
      analysis.classification.style + " стиль",
      "classification-label",
    ),
    node(
      "span",
      analysis.classification.essay_type + " эссе",
      "classification-label",
    ),
    node("p", analysis.classification.explanation),
  );
  quotes($("classification"), analysis.classification.quotes);
  $("criteria").replaceChildren();
  Object.keys(names).forEach((key) => {
    const value = analysis.criteria[key];
    const card = node("article", undefined, "panel criterion"),
      head = node("div", undefined, "criterion-head");
    head.append(
      node("h3", names[key]),
      node("strong", `${value.score} / ${data.max_scores[key]}`),
    );
    const bar = node("progress");
    bar.max = data.max_scores[key];
    bar.value = value.score;
    bar.setAttribute("aria-label", names[key]);
    card.append(head, bar, node("p", value.explanation));
    quotes(card, value.quotes);
    list(card, "Кемшіліктер", value.weaknesses);
    list(card, "Ұсыныстар", value.recommendations);
    $("criteria").append(card);
  });
  $("structure-map").replaceChildren();
  Object.entries(analysis.structure_map).forEach(([key, items]) => {
    $("structure-map").append(node("p", elements[key], "label"));
    quotes($("structure-map"), items);
  });
  $("suggestions").replaceChildren();
  list($("suggestions"), "Артықшылықтар", analysis.strengths);
  list($("suggestions"), "Назар аударыңыз", analysis.weaknesses);
  list($("suggestions"), "Жақсарту ұсыныстары", analysis.recommendations);
  $("corrections").replaceChildren();
  if (!analysis.language_corrections.length)
    $("corrections").append(
      node("p", "Нақты тілдік қате анықталмады.", "muted"),
    );
  analysis.language_corrections.forEach((item) => {
    quotes($("corrections"), [item.original]);
    $("corrections").append(
      node("p", "Түзету: " + item.corrected),
      node("p", item.explanation, "muted"),
    );
  });
}
function showTopicAlignment(
  alignment,
  section = { status: alignment ? "ready" : "not_run" },
) {
  const card = $("topic-alignment");
  card.className = "panel topic-alignment";
  card.replaceChildren(node("h3", "Тақырыпқа сәйкестік"));
  if (section.status !== "ready" || !alignment) {
    if (section.status === "pending")
      card.append(
        node("p", "Тақырып сәйкестігі тексеріліп жатыр… Дайын баға сақталады."),
      );
    else {
      card.classList.add("topic-warning");
      card.append(
        node(
          "p",
          section.error || "Бұл талдауда тақырып сәйкестігі тексерілмеген.",
        ),
        retryButton("topic", section),
      );
    }
    card.append(
      node(
        "p",
        "Тексеру орындалмағаны тақырып сәйкес емес дегенді білдірмейді.",
        "muted",
      ),
    );
    return;
  }
  const labels = {
    matched: "Тақырыпқа сәйкес",
    partial: "Тақырыпқа ішінара сәйкес",
    mismatched: "Эссе тақырыпқа сай емес",
    undetermined: "Сәйкестік анықталмады",
  };
  if (["partial", "mismatched"].includes(alignment.status))
    card.classList.add("topic-warning");
  card.append(
    node("strong", labels[alignment.status]),
    node("p", alignment.explanation),
  );
  quotes(card, alignment.quotes);
  if (alignment.suggested_title)
    card.append(
      node("p", "Мәтінге лайық ұсынылатын тақырып:", "label"),
      node("p", alignment.suggested_title, "suggested-title"),
    );
  card.append(
    node("p", "Тақырыпқа сәйкестік үшін балл шегерілмейді.", "muted"),
  );
}
function renderAnnotations(data) {
  const source = $("annotated-text"),
    details = $("annotation-details");
  source.replaceChildren();
  details.replaceChildren();
  $("annotation-legend").replaceChildren();
  Object.entries(names).forEach(([key, name]) =>
    $("annotation-legend").append(
      node("span", name, `legend-item feedback-${key}`),
    ),
  );
  const chars = Array.from(data.essay_text),
    findings = data.annotations || [];
  if (!findings.length) {
    source.textContent = data.essay_text;
    details.append(
      node(
        "p",
        data.analysis
          ? "Нақты үзіндіге бекітілген ескерту жоқ. Жалпы ұсыныстарды төменнен оқыңыз."
          : "Мәтіндік ескертулер бағалау дайын болғанда көрсетіледі.",
        "muted",
      ),
    );
    return;
  }
  const boundaries = [
    ...new Set([
      0,
      chars.length,
      ...findings.flatMap((item) => [item.start, item.end]),
    ]),
  ].sort((a, b) => a - b);
  for (let i = 0; i < boundaries.length - 1; i++) {
    const start = boundaries[i],
      end = boundaries[i + 1],
      text = chars.slice(start, end).join(""),
      active = findings.filter(
        (item) => item.start <= start && item.end >= end,
      );
    if (!active.length) {
      source.append(document.createTextNode(text));
      continue;
    }
    const mark = node("mark", text, `feedback-${active[0].category}`);
    mark.tabIndex = 0;
    mark.setAttribute("role", "button");
    mark.setAttribute(
      "aria-label",
      active.map((item) => names[item.category]).join(", ") + ": " + text,
    );
    const open = () => showFeedback(active, data);
    mark.onclick = open;
    mark.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    };
    source.append(mark);
  }
  showFeedback([findings[0]], data);
}
function showFeedback(findings, data) {
  $("annotation-details").replaceChildren();
  findings.forEach((item) => {
    const card = node("article", undefined, "feedback-card");
    card.append(node("p", names[item.category], "label"));
    quotes(card, [item.quote]);
    card.append(
      node("p", item.explanation),
      node("p", "Ұсыныс: " + item.suggestion),
    );
    if (item.occurrences > 1)
      card.append(
        node(
          "p",
          `Бұл үзінді ${item.occurrences} рет кездеседі. Алғашқы орны белгіленді.`,
          "muted",
        ),
      );
    card.append(
      button(
        "Осы үзіндіні түзету",
        () => startRevision(data, item),
        "secondary",
      ),
    );
    $("annotation-details").append(card);
  });
}
function startRevision(data, annotation = null) {
  if (busy) return;
  if (parentId !== data.id) {
    const changed =
      $("essay").value.trim() && $("essay").value.trim() !== data.essay_text;
    if (
      changed &&
      !confirm(
        "Сақталмаған мәтін осы эссенің нұсқасымен ауыстырылады. Жалғастырасыз ба?",
      )
    )
      return;
    $("title").value = data.title;
    $("essay").value = data.essay_text;
  }
  parentId = data.id;
  submission = null;
  $("revision-info").hidden = false;
  $("revision-info").replaceChildren(
    node(
      "p",
      `№${data.id} эссенің түзетілген нұсқасы. Бастапқы мәтін сақталады; жаңа талдау онымен салыстырылады.`,
    ),
    button("Нұсқамен байланысты тоқтату", () => {
      parentId = null;
      $("revision-info").hidden = true;
    }),
  );
  $("consent").checked = false;
  count();
  $("essay").focus();
  if (annotation && $("essay").value === data.essay_text) {
    const chars = Array.from(data.essay_text);
    $("essay").setSelectionRange(
      chars.slice(0, annotation.start).join("").length,
      chars.slice(0, annotation.end).join("").length,
    );
  }
  scrollToElement($("essay-form"));
}
async function loadComparison(data) {
  const token = ++comparisonToken;
  try {
    const versions = await api(`/api/essays/${data.id}/versions`);
    if (token !== comparisonToken || activeResult?.id !== data.id) return;
    const oldValue = $("compare-version").value;
    $("compare-version").replaceChildren();
    versions
      .filter((item) => item.id !== data.id)
      .forEach((item) => {
        const option = node(
          "option",
          `№${item.id} · ${item.total_score ?? "—"}/100 · ${item.title}`,
        );
        option.value = String(item.id);
        $("compare-version").append(option);
      });
    if (
      [...$("compare-version").options].some(
        (option) => option.value === oldValue,
      )
    )
      $("compare-version").value = oldValue;
    const before = $("compare-version").value;
    if (!before) {
      $("comparison-content").replaceChildren(
        node("p", "Алдыңғы нұсқа жойылған немесе қолжетімсіз."),
      );
      return;
    }
    const comparison = await api(
      `/api/essays/${data.id}/comparison?before_id=${before}`,
    );
    if (token !== comparisonToken || activeResult?.id !== data.id) return;
    renderComparison(comparison);
  } catch (e) {
    if (token === comparisonToken)
      $("comparison-content").replaceChildren(node("p", e.message));
  }
}
function renderComparison(data) {
  const root = $("comparison-content");
  root.replaceChildren(node("p", data.notice, "muted"));
  if (data.model_changed) {
    root.append(
      node(
        "p",
        `Нұсқалар әртүрлі модельдермен бағаланған: ${data.before_model} → ${data.after_model}. Балл айырмасын тек мәтіннің жақсаруымен түсіндіруге болмайды.`,
        "muted",
      ),
    );
  }
  if (data.total_delta !== null)
    root.append(
      node(
        "p",
        `Жалпы балл өзгерісі: ${data.total_delta > 0 ? "+" : ""}${data.total_delta}`,
        "score-delta",
      ),
    );
  else
    root.append(
      node(
        "p",
        "Баллдарды салыстыру үшін екі нұсқаның да бағасы дайын болуы керек. Мәтін айырмасы қазірдің өзінде көрсетіледі.",
        "muted",
      ),
    );
  if (!data.text_changed)
    root.append(
      node(
        "p",
        "Мәтін өзгермеген. Қайта бағалаудағы айырмашылық AI бағасының ауытқуы болуы мүмкін.",
      ),
    );
  if (data.score_changes.length) {
    const table = node("table", undefined, "score-table"),
      head = node("tr");
    ["Критерий", "Бұрын", "Қазір", "Айырма"].forEach((text) =>
      head.append(node("th", text)),
    );
    const thead = node("thead");
    thead.append(head);
    table.append(thead);
    const tbody = node("tbody");
    data.score_changes.forEach((item) => {
      const row = node("tr");
      [
        names[item.criterion],
        item.before,
        item.after,
        (item.delta > 0 ? "+" : "") + item.delta,
      ].forEach((text) => row.append(node("td", text)));
      tbody.append(row);
    });
    table.append(tbody);
    root.append(table);
  }
  const texts = node("div", undefined, "diff-grid");
  [
    ["Алдыңғы нұсқа", data.before_parts],
    ["Түзетілген нұсқа", data.after_parts],
  ].forEach(([title, parts]) => {
    const col = node("div");
    col.append(node("h4", title));
    const body = node("div", undefined, "source-text diff-text");
    parts.forEach((part) =>
      body.append(
        node(
          part.kind === "removed"
            ? "del"
            : part.kind === "added"
              ? "ins"
              : "span",
          part.text,
        ),
      ),
    );
    col.append(body);
    texts.append(col);
  });
  root.append(texts);
  if (data.observations.length) {
    const details = node("details");
    details.append(node("summary", "Алдыңғы ескертулерге қатысты өзгерістер"));
    data.observations.forEach((item) =>
      details.append(
        node("blockquote", item.quote),
        node(
          "p",
          item.status === "revised_quote"
            ? "Үзінді өзгертілген. Түзетудің сапасын жаңа талдаудан тексеріңіз."
            : "Үзінді мәтінде сақталған. Жаңа ескертулерді де қараңыз.",
        ),
      ),
    );
    root.append(details);
  }
  const reasoning = node("details");
  reasoning.append(node("summary", "Аргументтер мен дәлелдер қалай өзгерді?"));
  [
    ["Аргументтер", data.argument_changes],
    ["Дәлелдер", data.evidence_changes],
  ].forEach(([title, change]) => {
    reasoning.append(node("h4", title), node("p", "Бұрын", "label"));
    quotes(reasoning, change.before);
    reasoning.append(node("p", "Қазір", "label"));
    quotes(reasoning, change.after);
  });
  root.append(reasoning);
  const remaining = node("details");
  remaining.append(node("summary", "Қазіргі нұсқадағы нақты ескертулер"));
  if (!data.remaining_feedback.length)
    remaining.append(
      node(
        "p",
        "Нақты үзіндіге бекітілген ескерту жоқ. Жалпы критерийлерді де тексеріңіз.",
        "muted",
      ),
    );
  data.remaining_feedback.forEach((item) => {
    quotes(remaining, [item.quote]);
    remaining.append(
      node("p", item.explanation),
      node("p", "Ұсыныс: " + item.suggestion),
    );
  });
  root.append(remaining);
  if (data.score_changes.length) {
    const details = node("details");
    details.append(
      node("summary", "Критерийлер бойынша бұрынғы және қазіргі ескертулер"),
    );
    data.score_changes.forEach((item) => {
      details.append(node("h4", names[item.criterion]));
      list(details, "Бұрын", item.before_feedback);
      list(details, "Қазір", item.after_feedback);
    });
    root.append(details);
  }
}
$("compare-version").onchange = () => {
  if (activeResult) loadComparison(activeResult);
};
async function follow(data, scroll = true) {
  clearTimeout(pollTimer);
  const token = ++viewToken;
  showResult(data, scroll);
  if (!hasPending(data)) {
    history();
    return;
  }
  let failures = 0;
  const poll = async () => {
    if (token !== viewToken) return;
    try {
      const next = await api(`/api/essays/${data.id}`);
      if (token !== viewToken) return;
      failures = 0;
      if (
        next.revision !== activeResult?.revision ||
        next.status !== activeResult?.status
      )
        showResult(next, false);
      if (hasPending(next)) pollTimer = setTimeout(poll, 1400);
      else await history();
    } catch (e) {
      if (token !== viewToken) return;
      if (++failures < 3) pollTimer = setTimeout(poll, 2500);
      else
        error(
          "Байланыс үзілді. «Нәтижені жаңарту» батырмасын немесе тарихты қолданыңыз. " +
            e.message,
        );
    }
  };
  pollTimer = setTimeout(poll, 1000);
}
async function retry(section) {
  if (busy || !activeResult) return;
  error();
  setBusy(true, "Тек сәтсіз бөлім қайта тексеруге жіберілуде…");
  try {
    const data = await api(
      `/api/essays/${activeResult.id}/retry/${section}?background=true`,
      { method: "POST" },
    );
    await follow(data, false);
  } catch (e) {
    error(e.message);
  } finally {
    setBusy(false);
    updateRetryButtons();
  }
}
async function openEssay(id, scroll = true) {
  if (busy) return;
  const token = ++viewToken;
  clearTimeout(pollTimer);
  error();
  try {
    const data = await api(`/api/essays/${id}`);
    if (token !== viewToken) return;
    parentId = null;
    $("revision-info").hidden = true;
    $("title").value = data.title;
    $("essay").value = data.essay_text;
    count();
    await follow(data, scroll);
  } catch (e) {
    if (token === viewToken) error(e.message);
  }
}
async function history(append = false) {
  if (!window.tekserUser) {
    $("history-list").replaceChildren(
      node(
        "p",
        "Жұмыстарыңызды сақтау және тарихты көру үшін аккаунтыңызға кіріңіз.",
        "muted",
      ),
    );
    const link = node("a", "Кіру немесе тіркелу", "text-button");
    link.href = "/auth?next=/";
    $("history-list").append(link);
    return;
  }
  try {
    const offset = append ? historyOffset : 0,
      items = await api(`/api/essays?limit=20&offset=${offset}`);
    if (!append) $("history-list").replaceChildren();
    if (!items.length && !append)
      $("history-list").append(node("p", "Әзірге талдау жоқ.", "muted"));
    items.forEach((item) => {
      const row = node("div", undefined, "history-row"),
        open = button("", () => openEssay(item.id), "history-open");
      open.append(
        node("strong", item.title),
        node(
          "small",
          `№${item.id} · ${item.total_score ?? "—"}/100 · ${item.status === "pending" ? "Тексерілуде" : item.status === "partial" ? "Ішінара дайын" : item.status === "failed" ? "Қайта тексеру қажет" : item.word_count + " сөз"}${item.parent_id ? " · түзетілген нұсқа" : ""} · ${new Date(item.created_at).toLocaleDateString("kk-KZ")}`,
        ),
      );
      const remove = button(
        "×",
        async () => {
          if (
            busy ||
            !confirm(
              "Эссе жойылады. Басқа нұсқалар сақталады. Жалғастырасыз ба?",
            )
          )
            return;
          try {
            await api(`/api/essays/${item.id}`, { method: "DELETE" });
            if (activeResult?.id === item.id) {
              ++viewToken;
              clearTimeout(pollTimer);
              activeResult = null;
              $("results").hidden = true;
              sessionStorage.removeItem("essay-result-id");
            }
            await history();
          } catch (e) {
            error(e.message);
          }
        },
        "text-button delete",
      );
      remove.setAttribute("aria-label", `${item.title} эссесін жою`);
      row.append(open, remove);
      $("history-list").append(row);
    });
    historyOffset = offset + items.length;
    $("more").hidden = items.length < 20;
  } catch (e) {
    if (!append)
      $("history-list").replaceChildren(node("p", e.message, "muted"));
  }
}
function count() {
  const words =
    $("essay").value.match(/[\p{L}\p{N}]+(?:[’'–-][\p{L}\p{N}]+)*/gu) || [];
  $("word-count").textContent = words.length + " сөз";
}
$("essay").addEventListener("input", count);
$("essay-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  await window.accountReady;
  if (!window.tekserUser) {
    sessionStorage.setItem(
      "tekser-draft",
      JSON.stringify({ title: $("title").value, essay_text: $("essay").value }),
    );
    location.assign("/auth?next=/");
    return;
  }
  error();
  setBusy(true, "Эссе сақталып, тексеруге жіберілуде…");
  const body = {
    title: $("title").value.trim(),
    essay_text: $("essay").value.trim(),
    parent_id: parentId,
  };
  if (
    activeResult &&
    body.title === activeResult.title &&
    body.essay_text === activeResult.essay_text
  ) {
    try {
      await follow(await api(`/api/essays/${activeResult.id}`));
    } catch (e) {
      error(e.message);
    } finally {
      setBusy(
        false,
        "Мәтін өзгермеген. Сақталған нәтиже көрсетілді; сәтсіз бөлімді жеке қайта тексеріңіз.",
      );
    }
    return;
  }
  const signature = JSON.stringify(body);
  if (submission?.signature !== signature)
    submission = { signature, key: crypto.randomUUID() };
  body.request_id = submission.key;
  try {
    const data = await api("/api/essays/analyze?background=true", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("revision-info").hidden = true;
    await follow(data);
    await history();
  } catch (e) {
    error(e.message);
  } finally {
    setBusy(false);
  }
});
$("reset").onclick = () => {
  if (busy) return;
  ++viewToken;
  ++comparisonToken;
  clearTimeout(pollTimer);
  parentId = null;
  submission = null;
  activeResult = null;
  comparisonRevision = null;
  sessionStorage.removeItem("essay-result-id");
  $("essay-form").reset();
  $("results").hidden = true;
  $("revision-info").hidden = true;
  error();
  count();
  $("title").focus();
};
$("revise").onclick = () => {
  if (activeResult) startRevision(activeResult);
};
$("reload-result").onclick = async () => {
  if (!activeResult) return;
  const id = activeResult.id,
    token = ++viewToken;
  clearTimeout(pollTimer);
  try {
    const data = await api(`/api/essays/${id}`);
    if (token === viewToken) await follow(data, false);
  } catch (e) {
    if (token === viewToken) error(e.message);
  }
};
$("refresh").onclick = () => history();
$("more").onclick = () => history(true);
$("upload-image").onclick = () => $("image").click();
$("image").addEventListener("change", async () => {
  const image = $("image").files[0];
  if (!image || busy) return;
  if (!window.tekserUser) {
    error("Суреттен мәтін тану үшін алдымен аккаунтыңызға кіріңіз.");
    $("image").value = "";
    return;
  }
  error();
  if (image.size > 8 * 1024 * 1024) {
    error("Сурет көлемі 8 МБ-тан аспауы керек.");
    $("image").value = "";
    return;
  }
  if (
    !confirm(
      "Сурет мәтінді тану үшін Google Gemini сервисіне жіберіледі. Жалғастырасыз ба?",
    )
  ) {
    $("image").value = "";
    return;
  }
  if (
    $("essay").value.trim() &&
    !confirm("Қазіргі мәтін танылған мәтінмен ауыстырылады. Жалғастырасыз ба?")
  ) {
    $("image").value = "";
    return;
  }
  setBusy(true, "Суреттегі мәтін танылып жатыр…");
  const form = new FormData();
  form.append("image", image);
  try {
    const data = await api("/api/ocr", { method: "POST", body: form });
    $("essay").value = data.text;
    count();
    setBusy(false, data.warning);
  } catch (e) {
    error(e.message);
    setBusy(false);
  } finally {
    $("image").value = "";
  }
});
window.accountReady.then(() => {
  history();
  const last = sessionStorage.getItem("essay-result-id");
  if (window.tekserUser && last && /^\d+$/.test(last))
    openEssay(last, !location.hash);
  else {
    try {
      const draft = JSON.parse(sessionStorage.getItem("tekser-draft"));
      if (draft) {
        $("title").value = draft.title;
        $("essay").value = draft.essay_text;
        count();
        sessionStorage.removeItem("tekser-draft");
      }
    } catch {
      sessionStorage.removeItem("tekser-draft");
    }
  }
});
