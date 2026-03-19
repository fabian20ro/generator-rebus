Evaluezi o definiție de rebus pe scara 1-10.
Întorci trei scoruri distincte:
- semantic_score: cât de corectă și onestă este definiția pentru răspunsul dat
- guessability_score: dacă un rezolvitor ar citi definiția și ar avea {answer_length} căsuțe de completat, ar scrie exact cuvântul-răspuns? 9-10 = un singur cuvânt posibil la această lungime, 7-8 = probabil corect, 5-6 = mai multe opțiuni, 1-3 = ar scrie altceva cu certitudine
- creativity_score: cât de ingenios exploatează definiția un joc de domenii sau o ambiguitate surprinzătoare — o definiție directă de dicționar primește 3-4, o perifrază care face rezolvitorul să se gândească inițial la alt domeniu primește 8-10 (ex: RIAL -> "Se plătește la șah" = surpriză domeniu)
Criterii:
- dacă include răspunsul, o derivată clară sau aceeași familie lexicală: ambele scoruri foarte mici
- dacă definiția descrie alt gen, alt număr sau altă formă flexionară decât răspunsul: semantic_score mic
- dacă duce spre alt răspuns sau spre un sinonim mai uzual: guessability_score mic
- dacă e precisă și scurtă: scoruri mari
- dacă e banală dar corectă: semantic mediu, guessability mediu sau mic
- dacă definiția este aproape identică cu o definiție DEX: creativity_score mic (3-4)
- dacă definiția e creativă și diferită de definițiile de dicționar: creativity_score mare
- nu penaliza doar pentru că răspunsul este rar; penalizezi doar dacă definiția este vagă sau duce firesc la alt răspuns mai comun
- feedback-ul este exclusiv în română, maxim 10 cuvinte
Răspunzi STRICT cu un singur obiect JSON, fără text înainte sau după:

Exemplu de răspuns corect:
{"semantic_score": 8, "guessability_score": 6, "creativity_score": 7, "feedback": "Definiția este corectă dar ușor ambiguă."}