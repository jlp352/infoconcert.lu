## Info sur Salle concert

Salles de concert Luxembourg
https://luxembourg.public.lu/fr/visiter/arts-et-culture/musique-salles-concerts.html


| Salle de concert | Lieu       | Site                         | Scrapper                                                       |
| ---------------- | ---------- | ---------------------------- | -------------------------------------------------------------- |
| Den Atelier      | Lux Ville  | https://www.atelier.lu/**    | Ok                                                             |
| Rockhal          | Esch       | https://rockhal.lu/          | Ok                                                             |
| Trifolion        | Echternach | https://trifolion.lu/        | Non ajouté	Pas assez de concerts                               |
| Cube 521         | Marnach    | https://www.cube521.lu/      | Non Ajouté	Plutôt musique classique, du jazz  musique du monde |
| Kulturfabrik     | Esch       | https://www.kulturfabrik.lu/ |                                                                |
| Neimënster       | Lux Ville  | https://www.neimenster.lu/   | Déjà sur le site de l'atelier                                  |
| Philharmonie     | Lux Ville  | https://www.philharmonie.lu/ | Beaucoup de musique Classique. Voir comment filtrer            |
| Opderschmelz     | Dudelange  | (https://opderschmelz.lu/)   | Non Ajouté	Pas assez de concerts                               |
| Rotondes         | Lux Ville  | https://www.rotondes.lu/     | Déjà sur le site de l'atelier                                  |
| Casino 2000      | Mondorf    | https://casino2000.lu/	      |                                                                |

## Reste à faire

#### Scrapper
		Recuperer Prix
		Mettre un extrait musical
		Filtre par genres et status
		Mettre Rockhal par tous comme salle de concert
		Bug Rockhal pour certain buy link n'apparait pas
		
		

#### Web
		Site Multi Langue (Fr, En, Ge) selection langue en function du navigateur
		Revoir Barre Menu. Mettre Logo
		Rajouter les mois
		Filtre pour afficher selon criteres
			type de musique
			type de salle
		Filtre pour afficher les nouveaux concerts depuis last visite
		Revoir design de la tuile concert
			en cliquant sur la tuile allez sur la page concert
			rajouter bouton pour ecouter un extrait
			Mettre un bouton more details sur la page concert
			Si concert Sold out, mettre un bouton vers Ticket Swap
		Message Cookies
		Voir les mentions légales à mettre en place pour un site internet

		Page Salle de concerts
		Supprimer artiste
		Page contacts	
				
			

#### General
		- [x] Trouver un nom au projet: Infoconcert.lu
		- [x] Trouver un nom de domaine: libre pour lu
		- [ ] créer logo
		- [x] Mettre GitHub pour la sauvegarde


## Python
		Scrapper
			Den Atelier: scrape_atelier_concerts.py	Den Atelier
			Rockhall: scrape_rockhal_concerts.py	Rockhall
			
			Usage:	
				python scrape_atelier_concerts.py                  # JSON (défaut)
			    python scrape_atelier_concerts.py -f csv           # CSV
			    python scrape_atelier_concerts.py -f csv --available-only

		Merge
			Merge csv ou json dans un seule fichier
			Les doublons sont retirées si même artist et date_live

			Usage:
				python merge.py -f json   → fusionne JSON/  → OUT/concerts.json
			    python merge.py -f csv    → fusionne CSV/   → OUT/concerts.csv	


