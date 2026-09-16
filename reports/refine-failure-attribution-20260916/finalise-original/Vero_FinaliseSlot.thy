theory Vero_FinaliseSlot
imports Vero_Support Vero_CapReads Vero_IrqPrimitives
begin


context begin interpretation Arch .

vero_prove "c0069" c0069_prove:
  "final_matters' (cteCap cte) \<Longrightarrow>
   corres (=) (invs and cte_wp_at ((=) cap) ptr)
               (invs' and cte_wp_at' ((=) cte) (cte_map ptr))
       (is_final_cap cap) (isFinalCapability cte)"

(* VERO START c0069 prove *)

oops

(* VERO END c0069 prove *)

vero_disprove "c0069" c0069_disprove:
  "final_matters' (cteCap cte) \<Longrightarrow>
   corres (=) (invs and cte_wp_at ((=) cap) ptr)
               (invs' and cte_wp_at' ((=) cte) (cte_map ptr))
       (is_final_cap cap) (isFinalCapability cte)"

(* VERO START c0069 disprove *)

oops

(* VERO END c0069 disprove *)

vero_prove "c0070" c0070_prove:
  "corres (\<lambda>rv rv'. final_matters' (cteCap cte) \<longrightarrow> rv = rv')
          (invs and cte_wp_at ((=) cap) ptr)
          (invs' and cte_wp_at' ((=) cte) (cte_map ptr))
       (is_final_cap cap) (isFinalCapability cte)"

(* VERO START c0070 prove *)

oops

(* VERO END c0070 prove *)

vero_disprove "c0070" c0070_disprove:
  "corres (\<lambda>rv rv'. final_matters' (cteCap cte) \<longrightarrow> rv = rv')
          (invs and cte_wp_at ((=) cap) ptr)
          (invs' and cte_wp_at' ((=) cte) (cte_map ptr))
       (is_final_cap cap) (isFinalCapability cte)"

(* VERO START c0070 disprove *)

oops

(* VERO END c0070 disprove *)

vero_prove "c0064" c0064_prove:
  "corres dc \<top> (cte_at' (cte_map slot))
    (return ()) (clearUntypedFreeIndex (cte_map slot))"

(* VERO START c0064 prove *)

oops

(* VERO END c0064 prove *)

vero_disprove "c0064" c0064_disprove:
  "corres dc \<top> (cte_at' (cte_map slot))
    (return ()) (clearUntypedFreeIndex (cte_map slot))"

(* VERO START c0064 disprove *)

oops

(* VERO END c0064 disprove *)

vero_prove "c0062" c0062_prove:
  "acap_relation cap cap' \<Longrightarrow> corres dc \<top> \<top> (arch_post_cap_deletion cap) (ARM_H.postCapDeletion cap')"

(* VERO START c0062 prove *)

by (simp add: arch_post_cap_deletion_def ARM_H.postCapDeletion_def)

(* VERO END c0062 prove *)

vero_disprove "c0062" c0062_disprove:
  "acap_relation cap cap' \<Longrightarrow> corres dc \<top> \<top> (arch_post_cap_deletion cap) (ARM_H.postCapDeletion cap')"

(* VERO START c0062 disprove *)

oops

(* VERO END c0062 disprove *)

vero_prove "c0065" c0065_prove:
  "corres dc \<top> \<top>
    (deleted_irq_handler irq)
    (deletedIRQHandler irq)"

(* VERO START c0065 prove *)

unfolding deleted_irq_handler_def deletedIRQHandler_def
by (rule Vero_IrqPrimitives.c0076_prove; simp add: irq_state_relation_def)

(* VERO END c0065 prove *)

vero_disprove "c0065" c0065_disprove:
  "corres dc \<top> \<top>
    (deleted_irq_handler irq)
    (deletedIRQHandler irq)"

(* VERO START c0065 disprove *)

oops

(* VERO END c0065 disprove *)

vero_prove "c0071" c0071_prove:
  "cap_relation cap cap' \<Longrightarrow> corres dc \<top> \<top> (post_cap_deletion cap) (postCapDeletion cap')"

(* VERO START c0071 prove *)

by (cases cap; auto simp: post_cap_deletion_def postCapDeletion_def arch_post_cap_deletion_def ARM_H.postCapDeletion_def c0065_prove)

(* VERO END c0071 prove *)

vero_disprove "c0071" c0071_disprove:
  "cap_relation cap cap' \<Longrightarrow> corres dc \<top> \<top> (post_cap_deletion cap) (postCapDeletion cap')"

(* VERO START c0071 disprove *)

oops

(* VERO END c0071 disprove *)

vero_prove "c0066" c0066_prove:
  "cap_relation info info' \<Longrightarrow> corres dc (einvs and cte_at slot) (invs' and cte_at' (cte_map slot))
             (empty_slot slot info) (emptySlot (cte_map slot) info')"

(* VERO START c0066 prove *)

oops

(* VERO END c0066 prove *)

vero_disprove "c0066" c0066_disprove:
  "cap_relation info info' \<Longrightarrow> corres dc (einvs and cte_at slot) (invs' and cte_at' (cte_map slot))
             (empty_slot slot info) (emptySlot (cte_map slot) info')"

(* VERO START c0066 disprove *)

oops

(* VERO END c0066 disprove *)

end

end
