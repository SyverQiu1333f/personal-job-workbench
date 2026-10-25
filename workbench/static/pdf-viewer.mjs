import * as pdfjsLib from './pdfjs/build/pdf.min.mjs';
import {pdfErrorText} from './pdf-errors.mjs';
pdfjsLib.GlobalWorkerOptions.workerSrc='/static/pdfjs/build/pdf.worker.min.mjs';
const viewer=document.querySelector('#viewer'),status=document.querySelector('#page-status');
const previous=document.querySelector('#previous'),next=document.querySelector('#next'),scale=document.querySelector('#scale');
let pdf,pageNumber=1,rendering=false;
previous.disabled=next.disabled=true;
const readError=()=>Object.assign(new Error(),{name:'LocalReadError'});
async function render(){
 if(!pdf||rendering)return;
 rendering=true;previous.disabled=next.disabled=true;document.querySelector('#error').textContent='';
 try{
  const page=await pdf.getPage(pageNumber),viewport=page.getViewport({scale:Number(scale.value)}),canvas=document.querySelector('#page');
  const pixelRatio=Math.min(devicePixelRatio||1,2);
  canvas.width=Math.floor(viewport.width*pixelRatio);canvas.height=Math.floor(viewport.height*pixelRatio);
  canvas.style.width=Math.floor(viewport.width)+'px';
  await page.render({canvasContext:canvas.getContext('2d'),viewport,transform:pixelRatio===1?null:[pixelRatio,0,0,pixelRatio,0,0]}).promise;
  status.textContent=`第 ${pageNumber} / ${pdf.numPages} 页`;
 }catch(e){document.querySelector('#error').textContent=pdfErrorText(e);}
 finally{rendering=false;previous.disabled=pageNumber<=1;next.disabled=pageNumber>=pdf.numPages;}
}
previous.onclick=()=>{if(pageNumber>1&&!rendering){pageNumber--;render()}};
next.onclick=()=>{if(pdf&&pageNumber<pdf.numPages&&!rendering){pageNumber++;render()}};
scale.onchange=render;
try{
 const bytes=new Uint8Array(Number(viewer.dataset.size));
 let offset=0;
 while(offset<bytes.length){
  const response=await fetch('/preview-data/'+viewer.dataset.fileId,{method:'POST',body:new URLSearchParams({csrf:viewer.dataset.csrf,data_epoch:viewer.dataset.epoch,offset:String(offset)})});
  if(!response.ok)throw readError();
  const part=await response.json();
  if(part.error)throw readError();
  const raw=atob(part.data);
  if(!raw.length)throw readError();
  for(let i=0;i<raw.length;i++)bytes[offset+i]=raw.charCodeAt(i);
  offset+=raw.length;
 }
 pdf=await pdfjsLib.getDocument({data:bytes,cMapUrl:'/static/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/pdfjs/standard_fonts/',isEvalSupported:false}).promise;
 await render();
}catch(e){status.textContent='预览未完成';document.querySelector('#error').textContent=pdfErrorText(e);}
