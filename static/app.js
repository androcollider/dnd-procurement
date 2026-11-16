
'use strict';
let db=[];
let view='list',currentId=null,editingOffer=null,query='',toastTimer;
const app=document.getElementById('app');
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=new Intl.NumberFormat('ru-RU',{minimumFractionDigits:0,maximumFractionDigits:2});
const money=n=>n===null?'Требует уточнения':fmt.format(n/100)+' ₽';
const date=s=>s?new Date(s+'T00:00:00Z').toLocaleDateString('ru-RU',{timeZone:'UTC'}):'Не уточнено';
function cents(value){const s=String(value??'').trim().replace(',','.');if(!/^\d+(\.\d{1,2})?$/.test(s))return null;const [a,b='']=s.split('.');const n=Number(a)*100+Number(b.padEnd(2,'0'));return Number.isSafeInteger(n)?n:null;}
function total(p,o){return o.total_cents ?? null;}
function arrival(o){return o.arrival || null;}
const purchase=()=>db.find(p=>p.id===currentId);

async function api(path,method='GET',body){
 const response=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 let value;try{value=await response.json();}catch(e){throw new Error('Сервер вернул непонятный ответ. Проверьте окно приложения.');}
 if(!response.ok)throw new Error(value.error||'Не удалось выполнить запрос.');return value;
}
function showError(error){const el=document.getElementById('server-error');el.textContent=error.message||String(error);el.hidden=false;}
function clearError(){document.getElementById('server-error').hidden=true;}
function replacePurchase(p){const i=db.findIndex(x=>x.id===p.id);if(i<0)db.unshift(p);else db[i]=p;return p;}
async function reload(){db=await api('/api/purchases');}
function toast(msg){const el=document.getElementById('toast');el.textContent=msg;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.textContent='',3500);}
async function navigate(next,id=currentId){
 clearError();
 if(id!==null && next!=='list')replacePurchase(await api('/api/purchases/'+id));
 if(next==='list')await reload();
 view=next;currentId=id;render();window.scrollTo(0,0);
}
function heading(title,sub){return `<div class="eyebrow">ЗАКУПКИ / ДНД ТРЕЙД</div><h1>${escapeHtml(title)}</h1><p class="sub">${escapeHtml(sub)}</p>`;}
function button(label,action,primary=false,attrs=''){return `<button type="button" class="${primary?'primary':''}" data-action="${action}" ${attrs}>${escapeHtml(label)}</button>`;}
function input(label,name,value='',type='text',attrs=''){return `<label>${label}<input name="${name}" type="${type}" value="${escapeHtml(value)}" ${attrs}></label>`;}
function fieldselect(label,name,value,options){return `<label>${label}<select name="${name}">${options.map(([v,l])=>`<option value="${v}" ${v===value?'selected':''}>${escapeHtml(l)}</option>`).join('')}</select></label>`;}
function matchBadge(o){return o.match==='yes'?'<span class="badge good">Соответствует</span>':o.match==='deviation'?'<span class="badge warn">Есть отклонения</span>':'<span class="badge warn">Не проверено</span>';}
function dateBadge(p,o){const a=arrival(o);return !a?'<span class="badge warn">Срок не уточнён</span>':`<span class="badge ${a>p.due?'bad':'good'}">${date(a)} · ${a>p.due?'Опоздание':'В срок'}</span>`;}
function list(){
 let html=heading('Закупки','Сравните предложения по ткани или изготовлению изделий.');
 html+=`<div class="toolbar"><input id="search" aria-label="Поиск закупки" placeholder="Поиск по названию или номеру заказа" value="${escapeHtml(query)}">${button('Создать закупку','create',true)}</div><div id="list-results"></div>`;
 return html;
}
function listResults(){const items=db.filter(p=>(p.name+' '+p.order).toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
 const el=document.getElementById('list-results');if(!items.length){el.innerHTML=`<div class="card empty"><h2>${db.length?'Ничего не найдено':'Закупок пока нет'}</h2><p class="sub">${db.length?'Измените поисковый запрос.':'Создайте первую закупку и внесите требования.'}</p>${button(db.length?'Сбросить поиск':'Создать закупку',db.length?'clear-search':'create',true)}</div>`;return;}
 el.innerHTML=`<div class="tablewrap"><table><thead><tr><th>Закупка</th><th>Тип</th><th>Количество</th><th>Получить до</th><th>Состояние</th><th></th></tr></thead><tbody>${items.map(p=>`<tr><td><b>${escapeHtml(p.name)}</b><br><small>${escapeHtml(p.order||'Без номера заказа')}</small></td><td>${p.type==='work'?'Изготовление / переработка':'Материал'}</td><td>${fmt.format(p.quantity)} ${escapeHtml(p.unit)}</td><td>${date(p.due)}</td><td><span class="badge ${p.decision?'good':''}">${p.decision?(p.decision.needsReview?'Проверить решение':'Выбор сохранён'):p.offers.length+' предложений'}</span></td><td>${button('Открыть','open',false,`data-id="${escapeHtml(p.id)}"`)}</td></tr>`).join('')}</tbody></table></div>`;
}
function formPurchase(){const p=purchase();const editing=!!p;
 return heading(editing?'Редактирование закупки':'Новая закупка','Количество, сроки и требования можно ввести вручную.')+`<form id="purchase-form" class="card"><h2>Основные сведения</h2><div class="grid">${input('Название закупки <span class="required">*</span>','name',p?.name||'','text','required maxlength="150" class="wide"')}${fieldselect('Тип закупки','type',p?.type||'material',[['material','Материал — ткань'],['work','Изготовление / переработка']])}${input('Количество *','quantity',p?.quantity||'','number','required min="0.001" max="1000000" step="0.001"')}${input('Единица измерения *','unit',p?.unit||'м','text','required maxlength="15"')}${input('Получить до *','due',p?.due||'','date','required min="2000-01-01" max="2100-12-31"')}${input('Номер заказа (необязательно)','order',p?.order||'','text','maxlength="80"')}<label class="wide">Требования к ткани или изделию<textarea name="requirements" maxlength="5000" placeholder="Состав, плотность, размеры, чья ткань и что включено в работы…">${escapeHtml(p?.requirements||'')}</textarea></label></div><p class="hint">Одна закупка — один материал или вид работ. Номер заказа вводится вручную.</p><div class="actions"><button class="primary" type="submit">${editing?'Сохранить изменения':'Создать закупку'}</button>${button('Отмена',editing?'detail':'home')}</div></form>`;
}
function details(){const p=purchase();if(!p)return list();let html=heading(p.name,`${p.type==='work'?'Изготовление / переработка':'Материал'} · ${p.order||'Без номера заказа'}`);
 html+=`<div class="metrics"><div class="metric"><b>${fmt.format(p.quantity)} ${escapeHtml(p.unit)}</b><span>Объём закупки</span></div><div class="metric"><b>${date(p.due)}</b><span>Требуемая дата</span></div><div class="metric"><b>${p.offers.length}</b><span>Предложения</span></div></div>`;
 if(p.decision)html+=`<div class="saved">${p.decision.needsReview?'Условия изменились — проверьте ранее принятое решение.':'Выбор исполнителя сохранён.'}</div>`;
 html+=`<div class="card"><h2>Техническое задание</h2><pre class="requirements">${escapeHtml(p.requirements||'Требования пока не указаны.')}</pre><div class="actions">${button('Редактировать закупку и ТЗ','edit-purchase')}</div></div><div class="toolbar">${button('Добавить предложение','add-offer',true)}${button('Сравнить предложения','compare',false,p.offers.length?'':'disabled')}</div>`;
 html+=p.offers.length?`<div class="tablewrap"><table><thead><tr><th>Компания</th><th>Полная стоимость</th><th>Получение</th><th>ТЗ</th><th></th></tr></thead><tbody>${p.offers.map(o=>`<tr><td><b>${escapeHtml(o.company)}</b></td><td class="money">${money(total(p,o))}</td><td>${dateBadge(p,o)}</td><td>${matchBadge(o)}</td><td>${button('Изменить','edit-offer',false,`data-id="${escapeHtml(o.id)}"`)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="card empty">Предложений пока нет. Добавьте условия первой компании.</div>';
 return html+button('К списку закупок','home');}
function formOffer(){const p=purchase();const o=p.offers.find(o=>o.id===editingOffer);return heading(o?'Редактирование предложения':'Новое предложение',`${p.name} · ${fmt.format(p.quantity)} ${p.unit}`)+`<form id="offer-form" class="card"><div class="grid">${input('Компания *','company',o?.company||'','text','required maxlength="150"')}${input('Условия оплаты','payment',o?.payment||'','text','maxlength="200"')}${input('Цена за '+escapeHtml(p.unit)+', ₽','price',o?.price??'','number','min="0" max="10000000" step="0.01" placeholder="Не уточнено"')}${input('Доставка всей партии, ₽','delivery',o?.delivery??'','number','min="0" max="1000000000" step="0.01" placeholder="Не уточнено"')}${input('Дополнительные расходы, ₽','extra',o?.extra??'','number','min="0" max="1000000000" step="0.01" placeholder="Не уточнено"')}${input('Дата готовности / отгрузки','ready',o?.ready||'','date','min="2000-01-01" max="2100-12-31"')}${input('Доставка, календарных дней','days',o?.days??'','number','min="0" max="3650" step="1" placeholder="Не уточнено"')}${fieldselect('Соответствие ТЗ','match',o?.match||'unknown',[['unknown','Не проверено'],['yes','Соответствует'],['deviation','Есть отклонения']])}<label class="wide">Комментарий<textarea name="comment" maxlength="3000" placeholder="Отклонения от ТЗ, состав работ, чья ткань…">${escapeHtml(o?.comment||'')}</textarea></label></div><div class="note">Пустое поле означает «не уточнено». Если доставка бесплатная или дополнительных расходов нет, явно введите 0. Все цены сравниваются в рублях на одинаковой основе.</div><div class="actions"><button type="submit" class="primary">Сохранить предложение</button>${button('Отмена','detail')}</div></form>`;}
function compare(){const p=purchase();if(!p.offers.length)return details();const chosen=p.decision?.offerId;
 return heading('Сравнение предложений',`${p.name} · ${fmt.format(p.quantity)} ${p.unit} · Получить до ${date(p.due)}`)+
 `<div class="tablewrap"><table><thead><tr><th>Компания</th><th>Цена / ед.</th><th>Доставка</th><th>Итого</th><th>Получение</th><th>ТЗ</th><th>Оплата</th></tr></thead><tbody>${p.offers.map(o=>`<tr class="${o.id===chosen?'selected':''}"><td><b>${escapeHtml(o.company)}</b>${o.comment?`<br><small>${escapeHtml(o.comment)}</small>`:''}</td><td class="money">${money(cents(o.price))}</td><td>${money(cents(o.delivery))}</td><td class="money"><b>${money(total(p,o))}</b></td><td>${dateBadge(p,o)}</td><td>${matchBadge(o)}</td><td>${escapeHtml(o.payment||'Не уточнено')}</td></tr>`).join('')}</tbody></table></div>`+
 `<div class="note warn">Итог = количество × цена + доставка + дополнительные расходы. Срок получения = готовность + календарные дни доставки. Соответствие ТЗ проверяет закупщик.</div>`+
 (p.decision?`<div class="saved">${p.decision.needsReview?'Решение требует повторной проверки.':'Решение сохранено.'}</div>`:'')+
 `<form id="decision-form" class="card"><h2>Выбор исполнителя</h2><div class="grid">${fieldselect('Компания *','offerId',chosen||'',[['','Выберите компанию'],...p.offers.map(o=>[o.id,o.company])])}<label>Обоснование выбора *<textarea name="reason" required maxlength="2000" placeholder="Почему выбран этот вариант?">${escapeHtml(p.decision?.reason||'')}</textarea></label></div><div id="decision-risk" class="hint"></div><label id="risk-consent" class="check" hidden><input type="checkbox" name="acceptRisks">Я учёл предупреждения и подтверждаю выбор.</label><div class="actions"><button type="submit" class="primary">Сохранить выбор</button>${button('К карточке закупки','detail')}</div></form>`;}
function render(){let body=view==='list'?list():view==='purchase'?formPurchase():view==='detail'?details():view==='offer'?formOffer():compare();app.innerHTML=body+'<div class="footer">Учебный MVP · Без регистрации и интеграций. Данные хранятся в локальной базе SQLite.</div>';if(view==='list')listResults();if(view==='compare')risk();}

function risk(){
 const form=document.getElementById('decision-form');if(!form)return;
 const p=purchase(),o=p.offers.find(o=>o.id===Number(form.elements.offerId.value));
 const el=document.getElementById('decision-risk'),consent=document.getElementById('risk-consent');
 consent.hidden=true;form.elements.acceptRisks.required=false;form.elements.acceptRisks.checked=false;
 if(!o){el.textContent='Выберите компанию из списка.';return;}
 const risks=o.risks;
 el.textContent=risks.length?risks.join('. ')+'. Учтите это в обосновании.':'По внесённым данным предложение соответствует ТЗ и сроку.';
 el.className=risks.length?'note warn':'hint';consent.hidden=!risks.length;form.elements.acceptRisks.required=!!risks.length;
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action]');if(!b||b.disabled)return;
 const action=b.dataset.action; b.disabled=true;
 try{
  if(action==='home'){editingOffer=null;await navigate('list',null);}
  else if(action==='create'){editingOffer=null;await navigate('purchase',null);}
  else if(action==='open')await navigate('detail',Number(b.dataset.id));
  else if(action==='detail')await navigate('detail');
  else if(action==='edit-purchase')await navigate('purchase');
  else if(action==='add-offer'){editingOffer=null;await navigate('offer');}
  else if(action==='edit-offer'){editingOffer=Number(b.dataset.id);await navigate('offer');}
  else if(action==='compare')await navigate('compare');
  else if(action==='clear-search'){query='';render();}
 }catch(error){showError(error);}finally{b.disabled=false;}
});
document.addEventListener('input',e=>{if(e.target.id==='search'){query=e.target.value;listResults();}});
document.addEventListener('change',e=>{
 if(e.target.name==='offerId')risk();
 if(e.target.name==='type'&&!purchase())e.target.form.elements.unit.value=e.target.value==='work'?'шт.':'м';
});
document.addEventListener('submit',async e=>{
 e.preventDefault();const f=e.target;if(!f.reportValidity())return;
 clearError();const data=Object.fromEntries(new FormData(f));
 const submit=f.querySelector('[type=submit]');submit.disabled=true;const oldText=submit.textContent;submit.textContent='Сохранение…';
 try{
  if(f.id==='purchase-form'){
   const previous=purchase();
   if(previous)data.revision=previous.revision;
   const p=await api('/api/purchases'+(previous?'/'+previous.id:''),previous?'PUT':'POST',data);
   replacePurchase(p);currentId=p.id;view='detail';render();toast('Закупка сохранена');
  }else if(f.id==='offer-form'){
   const p=purchase(),o=p.offers.find(o=>o.id===editingOffer);
   if(o)data.revision=o.revision;
   const updated=await api('/api/purchases/'+p.id+'/offers'+(o?'/'+o.id:''),o?'PUT':'POST',data);
   replacePurchase(updated);editingOffer=null;view='detail';render();toast('Предложение сохранено');
  }else if(f.id==='decision-form'){
   const p=purchase(),o=p.offers.find(o=>o.id===Number(data.offerId));
   if(!o)throw new Error('Выберите исполнителя.');
   if(!data.reason.trim())throw new Error('Укажите обоснование выбора.');
   const updated=await api('/api/purchases/'+p.id+'/decision','PUT',{
    offerId:o.id,reason:data.reason,purchaseRevision:p.revision,offerRevision:o.revision,acceptRisks:!!f.elements.acceptRisks.checked
   });
   replacePurchase(updated);render();toast('Выбор исполнителя сохранён');
  }
 }catch(error){showError(error);}finally{submit.disabled=false;submit.textContent=oldText;}
});
async function start(){try{app.innerHTML='<p class="saving">Загружаем закупки…</p>';await reload();render();}catch(error){app.innerHTML='<div class="card"><h2>Нет связи с локальным сервером</h2><p>Запустите приложение и обновите страницу.</p></div>';showError(error);}}
start();
