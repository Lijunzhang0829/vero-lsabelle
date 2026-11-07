(* Transactional batches over a real, audited Isabelle prefix. No admissions. *)
local
  val root = Path.explode "/worker";
  fun path s = Path.append root (Path.basic s);
  fun read s = File.read (path s);
  fun write s text = File.write (path s) text;
  fun publish name text =
    (write (name ^ ".tmp") text;
     OS.FileSys.rename {old=Path.implode (path (name ^ ".tmp")),
                        new=Path.implode (path name)});
  fun element name text = XML.Elem ((name, []), [XML.Text text]);
  val imports = split_lines (read "imports");
  val theory_name = read "theory_name";
  val worker_id = read "worker_id";
  val parents = map Thy_Info.get_theory imports;
  val thy = Theory.begin_theory (theory_name, Position.none) parents;
  val baseline = map #1 (Thm.all_axioms_of thy);
  val active = Unsynchronized.ref (NONE : string option);
  val completed = Unsynchronized.ref ([] : (string * string) list);
  fun command_position tr = the_default "" (Option.map Int.toString (Position.line_of (Toplevel.pos_of tr)));
  fun save_state tr st = (case !active of NONE => () | SOME stem =>
    if not (Toplevel.is_proof st) then () else
    let val {context, goal, ...} = Proof.goal (Toplevel.proof_of st);
        fun term t = YXML.content_of (Syntax.string_of_term context t);
        fun element name text = XML.Elem ((name, []), [XML.Text text]);
        val goals = map (element "goal" o term) (Thm.prems_of goal);
        val assumptions = map (element "assumption" o term o Thm.term_of) (Assumption.all_assms_of context);
        val commands = map (fn (name, line) => XML.Elem (("command", [("name",name),("line",line)]), [])) (rev (!completed));
    in write (stem ^ ".proof-state.xml") (XML.string_of
      (XML.Elem (("proof_state", [("origin","before_failed_command"),
        ("command",Toplevel.name_of tr),("line",command_position tr)]),
        [XML.Elem (("goals",[]),goals), XML.Elem (("assumptions",[]),assumptions),
         XML.Elem (("executed_commands",[]),commands)]))) end);
  fun execute position text state =
    let val theory = Toplevel.theory_of state;
        val transitions = Outer_Syntax.parse_text theory (fn () => theory) position text;
        fun step tr st =
          let val started = Time.now ();
              val out = Exn.capture (Timeout.apply
                (Time.fromReal (the (Real.fromString (read "command_timeout"))))
                (Toplevel.command_exception true tr)) st;
          in writeln ("REGEN_COMMAND " ^ Toplevel.name_of tr ^ " " ^
               LargeInt.toString (Time.toMilliseconds (Time.- (Time.now (), started))));
             (case out of Exn.Res _ => completed := (Toplevel.name_of tr, command_position tr) :: !completed
              | Exn.Exn _ => ignore (Exn.capture (fn () => save_state tr st) ()));
             Exn.release out end;
    in fold step transitions state end;
  fun audit names st =
    let val _ = if Toplevel.is_theory st then () else error "Unclosed proof/context";
        val finished = execute Position.none "end" st;
        val theory = Toplevel.end_theory Position.none finished;
        val _ = if forall (member (op =) baseline) (map #1 (Thm.all_axioms_of theory))
                then () else error "New axioms in batch";
        fun fact name =
          let val thms = Global_Theory.get_thms theory name;
              val _ = if null thms then error "Empty audited fact" else ();
              val _ = Thm.consolidate thms;
              val _ = if Thm_Deps.has_skip_proof thms then error "skip_proof dependency" else ();
              val _ = if null (Thm_Deps.all_oracles thms) then () else error "oracle dependency";
          in writeln ("REGEN_BATCH_CLEAN " ^ name) end;
    in List.app fact names end;
  val initial_text = read "prefix";
  val initial = execute Position.none initial_text (Toplevel.make_state (SOME thy));
  val _ = audit (split_lines (read "prefix_facts")) initial;
  val base = Unsynchronized.ref initial;
  val base_text = Unsynchronized.ref initial_text;
  val pending = Unsynchronized.ref (NONE : (Toplevel.state * string) option);
  fun publish_audit stem delta names = publish (stem ^ ".audit.xml")
    (XML.string_of (XML.Elem (("batch_audit", [("version","2"),
      ("worker",worker_id),("request",stem),("theory",theory_name)]),
      [element "prefix" (!base_text), element "delta" delta,
       XML.Elem (("imports",[]), map (element "import") imports),
       XML.Elem (("facts",[]), map (element "fact") names)])));
  val _ = write "ready" "ready";
  fun loop n =
    if File.exists (path "stop") then ()
    else if not (File.exists (path (Int.toString n ^ ".request")))
    then (OS.Process.sleep (Time.fromMilliseconds 20); loop n)
    else let
      val stem = Int.toString n;
      val _ = (active := SOME stem; completed := []);
      val result = Exn.capture (fn () =>
        if read (stem ^ ".action") = "commit" then
          (case !pending of NONE => error "No pending audited batch"
           | SOME (st, text) => (base := st; base_text := text; pending := NONE; "COMMITTED"))
        else let
          val _ = pending := NONE;
          val position = Position.line_file
            (the (Int.fromString (read (stem ^ ".line"))))
            ("/candidate/" ^ read "theory_name" ^ ".thy");
          val delta = read (stem ^ ".request");
          val names = split_lines (read (stem ^ ".facts"));
          val st = Timeout.apply
            (Time.fromReal (the (Real.fromString (read "candidate_timeout"))))
            (fn () =>
              let val next = execute position delta (!base);
                  val _ = audit names next;
              in next end) ();
          (* Native evidence is complete before the response becomes visible.
             stdout remains diagnostic and may be delivered asynchronously. *)
          val _ = publish_audit stem delta names;
          val _ = pending := SOME (st, !base_text ^ delta);
        in "GREEN" end) ();
      val answer = (case result of Exn.Res text => text
        | Exn.Exn (Timeout.TIMEOUT _) => "TIMEOUT\nCandidate execution and audit exceeded the time budget"
        | Exn.Exn exn => "RED\n" ^ Runtime.exn_message exn);
      val _ = publish (stem ^ ".response") answer;
    in loop (n+1) end;
in val _ = loop 1 end;
