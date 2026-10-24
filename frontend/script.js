const $ = id => document.getElementById(id);
const names = {structure:'Құрылым',argumentation:'Аргументация',logic:'Логика',evidence:'Дәлелдер',language:'Тілдік сапа'};
const elements = {introduction:'Кіріспе',thesis:'Тезис',arguments:'Аргументтер',evidence:'Дәлелдер',counterargument:'Қарсы пікір',conclusion:'Қорытынды'};
let busy = false;
let historyOffset = 0;
function node(tag, text, className) { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(className) el.className=className; return el; }
function list(parent, title, items) { parent.append(node('p',title,'label')); const ul=node('ul'); if(!items.length) ul.append(node('li','Анықталмады')); else items.forEach(text=>ul.append(node('li',text))); parent.append(ul); }
function quotes(parent, items) { if(!items.length) parent.append(node('p','Анықталмады','muted')); items.forEach(text=>parent.append(node('blockquote',text))); }
function error(message='') { $('error').textContent=message; $('error').hidden=!message; }
function setBusy(value, message='') { busy=value; $('analyze').disabled=value; $('reset').disabled=value; $('image').disabled=value; $('button-text').textContent=value?'Өңделуде…':'Эссені талдау'; $('status').textContent=message; $('essay-form').setAttribute('aria-busy',String(value)); }
async function api(path, options={}) {
  const response=await fetch(path,{...options,signal:AbortSignal.timeout(190000)});
  if(response.status===204) return null;
  let data; try { data=await response.json(); } catch { throw new Error('Серверден жауап алынбады. Кейінірек қайталаңыз.'); }
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Сұрауды орындау мүмкін болмады.');
  return data;
}
function showResult(data) {
  const analysis=data.analysis;
  $('results').hidden=false; $('results-title').textContent=data.title;
  $('summary').textContent=analysis.summary; $('total-score').textContent=data.total_score;
  $('classification').replaceChildren(node('span',analysis.classification.style+' стиль','classification-label'),node('span',analysis.classification.essay_type+' эссе','classification-label'),node('p',analysis.classification.explanation));
  quotes($('classification'),analysis.classification.quotes);
  $('criteria').replaceChildren();
  Object.entries(analysis.criteria).forEach(([key,value])=>{
    const card=node('article',undefined,'panel criterion'), head=node('div',undefined,'criterion-head');
    head.append(node('h3',names[key]),node('strong',`${value.score} / ${data.max_scores[key]}`));
    const bar=node('progress');bar.max=data.max_scores[key];bar.value=value.score;bar.setAttribute('aria-label',names[key]);
    card.append(head,bar,node('p',value.explanation)); quotes(card,value.quotes);
    list(card,'Кемшіліктер',value.weaknesses);list(card,'Ұсыныстар',value.recommendations);$('criteria').append(card);
  });
  $('structure-map').replaceChildren();Object.entries(analysis.structure_map).forEach(([key,items])=>{ $('structure-map').append(node('p',elements[key],'label'));quotes($('structure-map'),items); });
  $('suggestions').replaceChildren();list($('suggestions'),'Артықшылықтар',analysis.strengths);list($('suggestions'),'Назар аударыңыз',analysis.weaknesses);list($('suggestions'),'Жақсарту ұсыныстары',analysis.recommendations);
  $('corrections').replaceChildren();if(!analysis.language_corrections.length) $('corrections').append(node('p','Нақты тілдік қате анықталмады.','muted'));
  analysis.language_corrections.forEach(item=>{ quotes($('corrections'),[item.original]);$('corrections').append(node('p','Түзету: '+item.corrected),node('p',item.explanation,'muted')); });
  $('markers').replaceChildren();if(!data.markers.length)$('markers').append(node('p','Маркерлер анықталмады.','muted'));
  data.markers.forEach(item=>{ $('markers').append(node('p',`${item.category}: «${item.text}» (${item.start}–${item.end})`),node('blockquote',item.sentence)); });
  $('results').scrollIntoView({behavior:'smooth'});
}
async function history(append=false) {
  try {
    const offset=append?historyOffset:0;
    const items=await api(`/api/essays?limit=20&offset=${offset}`);
    if(!append)$('history-list').replaceChildren();
    if(!items.length&&!append)$('history-list').append(node('p','Әзірге талдау жоқ.','muted'));
    items.forEach(item=>{
      const row=node('div',undefined,'history-row'),open=node('button',undefined,'history-open');open.type='button';
      open.append(node('strong',item.title),node('small',`${item.total_score}/100 · ${item.word_count} сөз · ${new Date(item.created_at).toLocaleDateString('kk-KZ')}`));
      open.onclick=async()=>{if(busy)return;error();try {const data=await api(`/api/essays/${item.id}`);$('title').value=data.title;$('essay').value=data.essay_text;count();showResult(data);}catch(e){error(e.message);}};
      const remove=node('button','×','text-button delete');remove.type='button';remove.setAttribute('aria-label',`${item.title} эссесін жою`);
      remove.onclick=async()=>{if(busy||!confirm('Эссе мен талдау біржола жойылады. Жалғастырасыз ба?'))return;try{await api(`/api/essays/${item.id}`,{method:'DELETE'});await history();}catch(e){error(e.message);}};
      row.append(open,remove);$('history-list').append(row);
    });
    historyOffset=offset+items.length;$('more').hidden=items.length<20;
  } catch(e) { if(!append)$('history-list').replaceChildren(node('p',e.message,'muted')); }
}
function count(){const words=$('essay').value.match(/[\p{L}\p{N}]+(?:[’'–-][\p{L}\p{N}]+)*/gu)||[];$('word-count').textContent=words.length+' сөз';}
$('essay').addEventListener('input',count);
$('essay-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;error();$('results').hidden=true;
  setBusy(true,'Gemini эссеңізді талдап жатыр. Бұл бір минуттан артық уақыт алуы мүмкін.');
  try { const data=await api('/api/essays/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('title').value.trim(),essay_text:$('essay').value.trim()})});showResult(data);await history(); }
  catch(e){error(e.name==='TimeoutError'?'Сұрау уақыты аяқталды. Тарихты жаңартып тексеріңіз.':e.message);}
  finally{setBusy(false);}
});
$('reset').onclick=()=>{if(busy)return;$('essay-form').reset();$('results').hidden=true;error();count();$('title').focus();};
$('refresh').onclick=()=>history();$('more').onclick=()=>history(true);
$('image').addEventListener('change',async()=>{
  const image=$('image').files[0];if(!image||busy)return;error();
  if(image.size>8*1024*1024){error('Сурет көлемі 8 МБ-тан аспауы керек.');$('image').value='';return;}
  if(!confirm('Сурет мәтінді тану үшін Google Gemini сервисіне жіберіледі. Жалғастырасыз ба?')){$('image').value='';return;}
  if($('essay').value.trim()&&!confirm('Қазіргі мәтін танылған мәтінмен ауыстырылады. Жалғастырасыз ба?')){$('image').value='';return;}
  setBusy(true,'Суреттегі мәтін танылып жатыр…');const form=new FormData();form.append('image',image);
  try{const data=await api('/api/ocr',{method:'POST',body:form});$('essay').value=data.text;count();setBusy(false,data.warning);}
  catch(e){error(e.message);setBusy(false);}finally{$('image').value='';}
});
history();
