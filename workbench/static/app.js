document.addEventListener('DOMContentLoaded',()=>{
 document.addEventListener('click',e=>{const toggle=e.target.closest('[data-tag-match]');if(!toggle)return;const mode=toggle.closest('form').querySelector('input[name="tag_mode"]');mode.value=mode.value==='all'?'any':'all';toggle.textContent=mode.value==='all'?'全部匹配':'任意匹配';toggle.setAttribute('aria-pressed',String(mode.value==='all'))});
 if(!sessionStorage.getItem('review-shown')){sessionStorage.setItem('review-shown','1');const reminder=document.createElement('div');reminder.className='notice';reminder.setAttribute('role','status');reminder.innerHTML='<strong>本次统一核对提醒</strong><p>请到招聘 App 核对沟通、简历发送、回复和面试邀约，再集中补记。关闭提醒不会标记任何事实为已核对。</p><button type="button">知道了，本次关闭</button>';reminder.querySelector('button').onclick=()=>reminder.remove();document.querySelector('header').after(reminder)}
 const dirtyForms=new Set();
 let submitting=false;
 function hasDirtyForm(except=null){return [...dirtyForms].some(form=>form.isConnected&&form!==except)}
 if(document.querySelector('main > .error'))document.querySelectorAll('.dirty-form').forEach(form=>dirtyForms.add(form));
 const searchDialog=document.getElementById('search-dialog');
 document.addEventListener('click',e=>{
  const open=e.target.closest('[data-search-open]'),close=e.target.closest('[data-search-close]'),parent=e.target.closest('[data-module-parent]');
  if(open&&searchDialog){searchDialog.showModal();open.setAttribute('aria-expanded','true')}
  if(close&&searchDialog)searchDialog.close();
  if(parent?.dataset?.inlineParent==='true'){
   e.preventDefault();
   if(hasDirtyForm()&&!confirm('还有未保存的输入，确定放弃并返回岗位列表？'))return;
   document.getElementById('edit-panel').replaceChildren();
   for(const form of dirtyForms)if(!form.isConnected)dirtyForms.delete(form);
   bindSticky();parent.hidden=true;delete parent.dataset.inlineParent;
   const anchor=new URL(parent.getAttribute('href'),location.origin).hash.slice(1),row=document.getElementById(anchor);
   if(row){row.tabIndex=-1;row.focus({preventScroll:true});row.scrollIntoView({block:'center'})}
  }
 });
 searchDialog?.addEventListener('close',()=>document.querySelector('[data-search-open]')?.setAttribute('aria-expanded','false'));
 searchDialog?.addEventListener('click',e=>{if(e.target===searchDialog){const r=searchDialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)searchDialog.close()}});
 const trackedBars=new Set();
 function measureSticky(){
  let height=0;
  document.querySelectorAll('.dirty-form .sticky').forEach(bar=>{
   const actual=bar.getBoundingClientRect().height;
   bar.closest('form').style.setProperty('--sticky-space',actual+'px');
   height=Math.max(height,actual);
  });
  document.documentElement.style.setProperty('--sticky-height',height+'px');
 }
 const stickyObserver=window.ResizeObserver?new ResizeObserver(measureSticky):null;
 function bindSticky(){
  for(const bar of trackedBars){if(!bar.isConnected){stickyObserver?.unobserve(bar);trackedBars.delete(bar)}}
  document.querySelectorAll('.dirty-form .sticky').forEach(bar=>{
   if(!trackedBars.has(bar)){trackedBars.add(bar);stickyObserver?.observe(bar)}
  });
  measureSticky();
 }
 function caretRect(field){
  const style=getComputedStyle(field),mirror=document.createElement('div');
  for(const name of ['fontFamily','fontSize','fontWeight','fontStyle','lineHeight','letterSpacing','wordSpacing','paddingTop','paddingBottom','paddingLeft','paddingRight','textIndent','tabSize','wordBreak','overflowWrap','direction'])mirror.style[name]=style[name];
  Object.assign(mirror.style,{position:'fixed',left:'-100000px',top:'0',width:field.clientWidth+'px',boxSizing:'border-box',whiteSpace:'pre-wrap',visibility:'hidden',border:'0',height:'auto'});
  const marker=document.createElement('span');
  marker.textContent=field.value.charAt(field.selectionStart)||'\u200b';
  mirror.append(document.createTextNode(field.value.slice(0,field.selectionStart)),marker);
  document.body.append(mirror);
  const top=field.getBoundingClientRect().top+parseFloat(style.borderTopWidth)+marker.offsetTop-field.scrollTop;
  const height=parseFloat(style.lineHeight)||parseFloat(style.fontSize)*1.5;
  mirror.remove();
  return {top,bottom:top+height,height};
 }
 function keepFieldVisible(field){
  if(!(field instanceof HTMLElement)||!field.matches('input,select,textarea')||document.activeElement!==field)return;
  const bar=field.closest('.dirty-form')?.querySelector('.sticky');
  if(!bar)return;
  const b=bar.getBoundingClientRect(),r=field.getBoundingClientRect();
  const top=12,bottom=Math.min(innerHeight-12,b.top>0&&b.top<innerHeight?b.top-12:innerHeight-12);
  if(bottom<=top)return;
  const available=bottom-top,label=field.closest('label')?.getBoundingClientRect();
  let target=label&&label.height<=available?label:r;
  if(r.height>available&&field instanceof HTMLTextAreaElement)target=caretRect(field);
  if(target.bottom>bottom)window.scrollBy({top:target.bottom-bottom,behavior:'instant'});
  else if(target.top<top)window.scrollBy({top:target.top-top,behavior:'instant'});
 }
 function revealActive(field){requestAnimationFrame(()=>keepFieldVisible(field))}
 bindSticky();
 window.addEventListener('resize',()=>{measureSticky();revealActive(document.activeElement)});
 document.addEventListener('focusin',e=>revealActive(e.target));
 document.addEventListener('input',e=>{const form=e.target.closest('.dirty-form');if(form)dirtyForms.add(form);revealActive(e.target)});
 document.addEventListener('change',e=>{const form=e.target.closest('.dirty-form');if(form)dirtyForms.add(form);revealActive(e.target)});
 document.addEventListener('keyup',e=>{if(e.target instanceof HTMLTextAreaElement)revealActive(e.target)});
 document.addEventListener('click',e=>{if(e.target instanceof HTMLTextAreaElement)revealActive(e.target)});
 window.addEventListener('beforeunload',e=>{if(hasDirtyForm()&&!submitting){e.preventDefault();e.returnValue='还有未保存的输入'}});
 document.addEventListener('submit',e=>{if(hasDirtyForm(e.target)&&!confirm('还有未保存的输入，确定放弃并继续此操作？')){e.preventDefault();return}submitting=true;sessionStorage.setItem('list-scroll',JSON.stringify({url:location.pathname+location.search,y:scrollY}));e.target.querySelectorAll('button').forEach(b=>{b.disabled=true});});
 document.addEventListener('htmx:beforeRequest',e=>{if(hasDirtyForm()&&!confirm('还有未保存的输入，确定放弃并切换记录？'))e.preventDefault()});
 document.addEventListener('htmx:afterSwap',e=>{if(e.target.id==='edit-panel'){for(const form of dirtyForms)if(!form.isConnected)dirtyForms.delete(form);const step=e.target.querySelector('[data-module-parent-url]'),parent=document.querySelector('[data-module-parent]');if(step&&parent){parent.setAttribute('href',step.dataset.moduleParentUrl);parent.setAttribute('aria-label','返回岗位列表');parent.setAttribute('title','返回岗位列表');parent.querySelector('[data-parent-label]').textContent='返回岗位列表';parent.hidden=false;parent.dataset.inlineParent='true'}bindSticky();e.target.scrollIntoView({block:'start',behavior:'smooth'})}});
 document.querySelectorAll('a.remember').forEach(a=>a.addEventListener('click',()=>sessionStorage.setItem('list-scroll',JSON.stringify({url:location.pathname+location.search,y:scrollY}))));
 let position=null;try{position=JSON.parse(sessionStorage.getItem('list-scroll')||'null')}catch{}
 function listUrl(value){const url=new URL(value,location.origin);url.searchParams.delete('saved');url.hash='';return url.pathname+url.search}
 if(['/jobs','/resumes','/materials'].includes(location.pathname)&&position&&typeof position.url==='string'&&listUrl(position.url)===listUrl(location.href)){requestAnimationFrame(()=>scrollTo(0,position.y))}
 if(location.hash){const current=document.getElementById(location.hash.slice(1));if(current){current.tabIndex=-1;current.focus({preventScroll:true});current.scrollIntoView({block:'center'})}}
 window.addEventListener('pageshow',()=>{submitting=false;document.querySelectorAll('button').forEach(b=>b.disabled=false)});
});
