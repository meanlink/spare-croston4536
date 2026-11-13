import {jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
function selectionPanel(r){
 const s=r.selection;
 if(!s)return '<section class="panel"><div class="section-title"><h2>候选方案比较</h2><span>历史结果</span></div><p class="empty">该结果由旧版本算法生成，未保留候选选参记录；其参数与结论保持原样、不重算。重新发起一次自动选参试算可获得候选得分与日期分界。</p></section>';
 if(s.mode==='manual')return '<section class="panel"><div class="section-title"><h2>选参口径</h2><span>手选模式（旧口径）</span></div><div class="method-sheet compact"><p class="eyebrow">MANUAL SELECTION</p><h3>'+esc(s.variant_label)+' · α='+esc(fmt(s.alpha))+'</h3><p>'+esc(s.label)+'</p></div></section>';
 const winner=s.winner;
 const cols=[
  {key:'rank',label:'排名'},
  {key:'variant_label',label:'偏差校正'},
  {key:'alpha',label:'平滑系数 α'},
  {key:'selection_mae',label:'选参窗口 MAE（仅较早日历）'},
  {key:'holdout_mae',label:'独立检验 MAE（末28天，不参与选参）'},
 ];
 const rows=(s.candidates||[]).map(c=>({rank:'#'+c.rank,variant_label:c.variant_label,alpha:fmt(c.alpha),selection_mae:fmt(c.selection_mae),holdout_mae:fmt(c.holdout_mae)}));
 const statusText=s.status==='ok'?'选参有效':(s.status==='sparse'?'证据稀疏，需人工复核':'证据不足，零预测占位');
 return '<section class="panel"><div class="section-title"><h2>候选方案自动比较</h2><span>'+rows.length+' 组候选 · '+esc(statusText)+'</span></div>'
  +'<div class="method-sheet compact"><p class="eyebrow">SPLIT / 日期分界</p><p>'+esc(s.split_rule)+'</p><p class="hint">选参段 '+esc(s.train_start)+' 至 '+esc(s.train_end)+'（'+s.train_days+' 天，含 '+s.train_nonzero_days+' 个非零领用日）；独立检验期 '+esc(s.holdout_start)+' 至 '+esc(s.holdout_end)+'（'+s.holdout_days+' 天，逐日预测只使用该日之前的数据）。</p></div>'
  +(winner
    ?'<div class="method-sheet compact"><p class="eyebrow">WINNER / 胜出原因</p><h3>'+esc(winner.variant_label)+' · α='+esc(fmt(winner.alpha))+'</h3><p>'+esc(s.winner_reason)+'</p><p class="hint">'+esc(s.tie_break_rule)+' 胜出参数随后用全部 '+s.train_days+'+'+s.holdout_days+' 天历史重训，生成下方未来预测。</p></div>'
    :'<div class="method-sheet compact"><p class="eyebrow">NO WINNER / 无可胜出方案</p><p>'+esc(s.winner_reason)+'</p></div>')
  +(rows.length?table(cols,rows,'证据不足，未生成候选评分。'):'<p class="empty">证据不足，未生成候选评分。</p>')
  +'<p class="hint">“选参窗口 MAE”是唯一用于排名的分数；“独立检验 MAE”仅供事后核验，防止在末 28 天上手调参数造成效果高估。</p></section>';
}
export async function results(id){
 workspace.jobs=await jobs.list();const j=id?await jobs.get(id):(workspace.jobs.find(j=>j.id===workspace.selectedJob)||workspace.jobs[0]);
 if(j&&!workspace.jobs.some(x=>x.id===j.id))workspace.jobs.push(j);
 if(!j)return heading('04 / REVIEW','结果复核','尚无分析结果。')+'<div class="empty">先到分析工作台，选择批次并提交一次计算。<a href="#/workbench">前往工作台 ↗</a></div>';
 workspace.selectedJob=j.id;const r=j.result||{};
 return heading('04 / REVIEW','结果复核与业务解释',j.name,'<button class="button" id="refresh-results">刷新结果 ↻</button>')+'<div class="result-selector"><label>分析记录<select id="select-job">'+workspace.jobs.map(x=>'<option value="'+x.id+'" '+(x.id===j.id?'selected':'')+'>'+esc(x.name)+' · '+esc(x.status)+'</option>').join('')+'</select></label>'+status(j.status)+'<span>'+date(j.created_at)+'</span><span class="approval">'+(j.approved?'已获业务批准':'待负责人复核')+'</span></div>'+(j.status==='completed'?'<section class="result-lead"><p class="eyebrow">COMPUTED FINDING</p><h2>'+esc(r.summary)+'</h2><div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div></section><section class="panel">'+chart(r.chart)+'</section>'+selectionPanel(r)+'<div class="two-columns"><section class="panel"><div class="section-title"><h2>业务解释</h2><span>由计算结果生成</span></div><ol class="questions">'+(r.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(r.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')+'</section><section class="method-sheet compact"><p class="eyebrow">REPRODUCIBILITY</p><h3>计算口径与参数</h3><p>'+esc(r.method||'')+'</p><pre>'+esc(JSON.stringify(j.params,null,2))+'</pre>'+(r.engine?'<p class="hint">算法版本：'+esc(r.engine)+'</p>':'<p class="hint">旧版本结果：保留原始参数与结论，升级后不重算、不改写。</p>')+(session.user.role==='admin'&&!j.approved?'<button class="primary" id="approve-job">批准此业务结论</button>':'')+'<p class="hint">仅批准结果可进入定时分发档案。</p></section></div><section class="panel"><div class="section-title"><h2>结果明细</h2><span>'+(r.table?.rows?.length||0)+' 行</span></div>'+table(r.table?.columns||[],r.table?.rows||[])+'</section>':'<section class="panel empty">'+(j.status==='failed'?'计算失败：'+esc(j.error)+'。请返回工作台调整参数或数据。':'后台正在执行计算。点击刷新结果查看最新进展。')+'<a href="#/workbench">返回工作台 ↗</a></section>');
}
export function bindResults(refresh){document.querySelector('#refresh-results')?.addEventListener('click',refresh);document.querySelector('#select-job')?.addEventListener('change',e=>{workspace.selectedJob=Number(e.target.value);location.hash='#/results/'+e.target.value;});document.querySelector('#approve-job')?.addEventListener('click',async()=>{try{await jobs.approve(workspace.selectedJob);notify('已批准，可创建分发计划');await refresh();}catch(e){notify(e.message,true);}});}
