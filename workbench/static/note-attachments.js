/* File selection and screenshot pasting share the native multipart file input. */
(()=>{
 const queues=new WeakMap(),limit=50_000_000;
 function bind(){document.querySelectorAll('[data-note-upload]').forEach(box=>{
  const input=box.querySelector('[data-note-files]');if(queues.has(input))return;
  let queue=[];queues.set(input,true);
  const status=box.querySelector('[data-note-status]'),list=box.querySelector('[data-note-pending]');
  function render(){
   const transfer=new DataTransfer();queue.forEach(file=>transfer.items.add(file));input.files=transfer.files;
   const oversized=queue.some(file=>file.size>limit);input.setCustomValidity(oversized?'单个附件不能超过 50MB，请移除超限文件。':'');
   status.textContent=oversized?'有文件超过 50MB，请移除后保存。':queue.length?`待保存 ${queue.length} 个附件`:'尚未选择新附件';
   list.replaceChildren();queue.forEach((file,index)=>{const row=document.createElement('li'),name=document.createElement('span'),remove=document.createElement('button');
    name.textContent=file.name+' · '+(file.size/1024).toFixed(1)+' KB';remove.type='button';remove.textContent='移除';remove.setAttribute('aria-label','移除待保存附件 '+file.name);
    remove.onclick=()=>{queue.splice(index,1);render();input.dispatchEvent(new Event('input',{bubbles:true}))};row.append(name,remove);list.append(row);
   });
  }
  input.addEventListener('change',()=>{queue.push(...input.files);render()});
  box.querySelector('[data-note-paste]').addEventListener('paste',event=>{
   const images=[...(event.clipboardData?.files||[])].filter(file=>['image/png','image/jpeg','image/webp'].includes(file.type));
   if(!images.length){status.textContent='剪贴板里没有可用的截图，请复制图片后重试，或直接选择文件。';return}
   event.preventDefault();const stamp=new Date().toISOString().replace(/[-:]/g,'').replace(/\..*/,'').replace('T','-');
   images.forEach((file,index)=>{const ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[file.type];queue.push(new File([file],`微信截图-${stamp}-${queue.length+index+1}.${ext}`,{type:file.type}))});
   render();input.dispatchEvent(new Event('input',{bubbles:true}));
  });
 });}
 document.addEventListener('DOMContentLoaded',bind);document.addEventListener('htmx:afterSwap',bind);
})();
