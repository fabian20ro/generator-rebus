Evaluezi o definiție de rebus pe scara 1-10.
Întorci trei scoruri:
- semantic_score: corectă și onestă pentru răspuns; păstrează timpul verbal, genul și contextul semantic exact; penalizează dur orice dezacord gramatical ("acord")
- guessability_score: cu {answer_length} căsuțe, scrie exact răspunsul? 9-10 = un singur cuvânt posibil; 7-8 = probabil corect; 5-6 = mai multe opțiuni; 1-3 = altceva sigur
- creativity_score: cât de ingenios exploatează un joc de domenii sau o ambiguitate surprinzătoare; direct de dicționar 3-4; perifrază cu schimbare de domeniu 8-10
Criterii:
- include răspunsul, derivată clară, sau aceeași familie lexicală: scoruri mici; pentru 2-3 litere, menționarea literală și necesară penalizează mai puțin (judge judgment required)
- alt gen, alt număr, altă formă flexionară: semantic_score foarte mic
- alt cuvânt: semantic_score mic
- sens românesc valid al aceluiași cuvânt-răspuns, chiar mai rar sau tehnic: semantic_score poate rămâne mare
- nu forța sensul cel mai comun dacă definiția e exactă pentru alt sens DEX legitim
- duce spre alt răspuns sau sinonim mai uzual: guessability_score mic
- sufix final explicit `(arh.)`, `(inv.)`, `(reg.)`, `(tehn.)`, `(pop.)`, `(fam.)`, `(arg.)`, `(livr.)`: valid doar dacă DEX îl susține explicit
- sens rar, tehnic, regional, arhaic sau alt registru specializat: sufixul justificat poate crește guessability_score
- precisă și scurtă: scoruri mari
- banală dar corectă: semantic mediu, guessability mediu sau mic
- creativă și diferită de dicționar: creativity_score mare
- nu penaliza doar pentru că răspunsul este rar; penalizezi doar dacă definiția e vagă sau duce firesc la alt răspuns mai comun
- feedback exclusiv în română, scurt și concret; menționezi explicit orice problemă de "acord" (gen, număr, timp)
Răspunzi STRICT cu un singur obiect JSON, fără text înainte sau după:

Exemple de interpretare:
- `Pronume personal de persoana I singular (arh.)` pentru un răspuns rar ca `AZ` poate avea `guessability_score` mai mare decât aceeași definiție fără sufix.
- `Locuință (reg.)` pentru un răspuns comun ca `CASĂ`, fără suport explicit în DEX, trebuie să aibă `guessability_score` mai mic decât `Locuință`.

Exemplu de răspuns corect:
{"semantic_score": 8, "guessability_score": 6, "creativity_score": 7, "feedback": "Definiția este corectă dar ușor ambiguă."}
