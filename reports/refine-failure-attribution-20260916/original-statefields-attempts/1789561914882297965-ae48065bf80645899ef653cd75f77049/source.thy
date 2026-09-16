theory Vero_StateFields
imports Vero_Support
begin


context begin interpretation Arch .

vero_prove "c0031" c0031_prove [corres]: "corres (=) \<top> \<top> (gets cur_thread) getCurThread"

(* VERO START c0031 prove *)

by (auto simp: getCurThread_def corres_gets state_relation_def)

(* VERO END c0031 prove *)

vero_disprove "c0031" c0031_disprove [corres]: "corres (=) \<top> \<top> (gets cur_thread) getCurThread"

(* VERO START c0031 disprove *)

oops

(* VERO END c0031 disprove *)

vero_prove "c0032" c0032_prove [corres]:
  "corres (=) \<top> \<top> (gets idle_thread) getIdleThread"

(* VERO START c0032 prove *)

by (auto simp: getIdleThread_def corres_gets state_relation_def)

(* VERO END c0032 prove *)

vero_disprove "c0032" c0032_disprove [corres]:
  "corres (=) \<top> \<top> (gets idle_thread) getIdleThread"

(* VERO START c0032 disprove *)

oops

(* VERO END c0032 disprove *)

vero_prove "c0150" c0150_prove: "corres (=) \<top> \<top> (gets cur_domain) (curDomain)"

(* VERO START c0150 prove *)

by (auto simp: curDomain_def corres_gets state_relation_def)

(* VERO END c0150 prove *)

vero_disprove "c0150" c0150_disprove: "corres (=) \<top> \<top> (gets cur_domain) (curDomain)"

(* VERO START c0150 disprove *)

oops

(* VERO END c0150 disprove *)

vero_prove "c0144" c0144_prove: "corres (=) \<top> \<top> (gets cur_domain) curDomain"

(* VERO START c0144 prove *)

by (rule c0150_prove)

(* VERO END c0144 prove *)

vero_disprove "c0144" c0144_disprove: "corres (=) \<top> \<top> (gets cur_domain) curDomain"

(* VERO START c0144 disprove *)

oops

(* VERO END c0144 disprove *)

vero_prove "c0149" c0149_prove:
  "corres (=) \<top> (\<lambda>s. ksCurDomain s \<le> maxDomain)
    (gets cur_domain) (if Suc 0 < numDomains then curDomain else return 0)"

(* VERO START c0149 prove *)

by (auto simp: curDomain_def corres_gets state_relation_def maxDomain_def numDomains_def corres_underlying_def gets_def get_def return_def bind_def)

(* VERO END c0149 prove *)

vero_disprove "c0149" c0149_disprove:
  "corres (=) \<top> (\<lambda>s. ksCurDomain s \<le> maxDomain)
    (gets cur_domain) (if Suc 0 < numDomains then curDomain else return 0)"

(* VERO START c0149 disprove *)

oops

(* VERO END c0149 disprove *)

vero_prove "c0152" c0152_prove:
  "corres (=) \<top> \<top> (gets domain_time) getDomainTime"

(* VERO START c0152 prove *)

by (auto simp: getDomainTime_def corres_gets state_relation_def)

(* VERO END c0152 prove *)

vero_disprove "c0152" c0152_disprove:
  "corres (=) \<top> \<top> (gets domain_time) getDomainTime"

(* VERO START c0152 disprove *)

oops

(* VERO END c0152 disprove *)

vero_prove "c0081" c0081_prove:
  "corres dc \<top> \<top> dec_domain_time decDomainTime"

(* VERO START c0081 prove *)

unfolding dec_domain_time_def decDomainTime_def
by (fastforce intro: corres_modify simp: state_relation_def)

(* VERO END c0081 prove *)

vero_disprove "c0081" c0081_disprove:
  "corres dc \<top> \<top> dec_domain_time decDomainTime"

(* VERO START c0081 disprove *)

oops

(* VERO END c0081 disprove *)

vero_prove "c0194" c0194_prove:
  "corres sched_act_relation \<top> \<top> (gets scheduler_action) getSchedulerAction"

(* VERO START c0194 prove *)

by (auto simp: getSchedulerAction_def corres_gets state_relation_def)

(* VERO END c0194 prove *)

vero_disprove "c0194" c0194_disprove:
  "corres sched_act_relation \<top> \<top> (gets scheduler_action) getSchedulerAction"

(* VERO START c0194 disprove *)

oops

(* VERO END c0194 disprove *)

vero_prove "c0206" c0206_prove:
  "sched_act_relation sa sa'
    \<Longrightarrow> corres dc \<top> \<top> (set_scheduler_action sa) (setSchedulerAction sa')"

(* VERO START c0206 prove *)

unfolding set_scheduler_action_def setSchedulerAction_def
by (fastforce intro: corres_modify simp: state_relation_def)

(* VERO END c0206 prove *)

vero_disprove "c0206" c0206_disprove:
  "sched_act_relation sa sa'
    \<Longrightarrow> corres dc \<top> \<top> (set_scheduler_action sa) (setSchedulerAction sa')"

(* VERO START c0206 disprove *)

oops

(* VERO END c0206 disprove *)

vero_prove "c0162" c0162_prove:
  "corres dc (valid_idle and valid_queues and valid_etcbs and pspace_aligned and pspace_distinct) \<top>
     (modify (cur_thread_update (\<lambda>_. t))) (setCurThread t)"

(* VERO START c0162 prove *)

unfolding setCurThread_def
by (fastforce intro: corres_modify simp: state_relation_def)

(* VERO END c0162 prove *)

vero_disprove "c0162" c0162_disprove:
  "corres dc (valid_idle and valid_queues and valid_etcbs and pspace_aligned and pspace_distinct) \<top>
     (modify (cur_thread_update (\<lambda>_. t))) (setCurThread t)"

(* VERO START c0162 disprove *)

oops

(* VERO END c0162 disprove *)

vero_prove "c0156" c0156_prove:
  "corres dc \<top> \<top> next_domain nextDomain"

(* VERO START c0156 prove *)

unfolding next_domain_def nextDomain_def Let_def dschDomain_def dschLength_def
by (fastforce intro: corres_modify simp: state_relation_def)

(* VERO END c0156 prove *)

vero_disprove "c0156" c0156_disprove:
  "corres dc \<top> \<top> next_domain nextDomain"

(* VERO START c0156 disprove *)

oops

(* VERO END c0156 disprove *)

end

end
