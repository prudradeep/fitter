(function(){
  var tabs=[].slice.call(document.querySelectorAll('.tab'));
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var dots=[].slice.call(document.querySelectorAll('.dots span'));
  var prev=document.getElementById('prev'), next=document.getElementById('next');
  var cur=0;
  function show(i,focus){
    cur=Math.max(0,Math.min(slides.length-1,i));
    slides.forEach(function(s,k){var on=k===cur; s.classList.toggle('active',on); s.hidden=!on;});
    tabs.forEach(function(t,k){var on=k===cur; t.setAttribute('aria-selected',on); t.tabIndex=on?0:-1;});
    dots.forEach(function(d,k){d.classList.toggle('on',k===cur);});
    prev.disabled=cur===0; next.disabled=cur===slides.length-1;
    if(focus) tabs[cur].focus();
  }
  tabs.forEach(function(t,k){t.addEventListener('click',function(){show(k);});});
  document.querySelector('.tabs').addEventListener('keydown',function(e){
    if(e.key==='ArrowRight'){show(cur+1,true);e.preventDefault();}
    if(e.key==='ArrowLeft'){show(cur-1,true);e.preventDefault();}
  });
  prev.addEventListener('click',function(){show(cur-1);});
  next.addEventListener('click',function(){show(cur+1);});
  var x0=null;
  document.querySelector('.wrap').addEventListener('touchstart',function(e){x0=e.touches[0].clientX;},{passive:true});
  document.querySelector('.wrap').addEventListener('touchend',function(e){
    if(x0===null)return; var dx=e.changedTouches[0].clientX-x0;
    if(Math.abs(dx)>60) show(cur+(dx<0?1:-1)); x0=null;
  });
  show(0);
})();
