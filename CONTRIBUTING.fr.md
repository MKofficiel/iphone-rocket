# Contribuer

[English](CONTRIBUTING.md) · **Français**

Merci de ton aide ! La contribution la plus précieuse aujourd'hui ne demande aucun code : **dis-nous si ça marche sur ton iPhone**, avec le modèle, la version d'iOS et la langue de Windows.

## Lancer l'outil sans iPhone

```
python rocket.py --demo
```

Le mode démo affiche une bibliothèque inventée. C'est le moyen de travailler sur l'interface, et la seule source autorisée pour les captures d'écran.

## Tests

Ni iPhone ni dépendance nécessaires :

```
python -m unittest discover -s tests
```

## Plan du projet

| Fichier | Rôle |
|---|---|
| `iphone.py` | Protocoles USB (usbmuxd, lockdownd, AFC). **Lecture seule.** |
| `rocket.py` | Serveur local, bibliothèque, copie, suppression, mode démo |
| `index.html` | Toute l'interface, sans étape de compilation |
| `locales/*.json` | Textes de l'interface |
| `wpd-map.cs` | Liste les fichiers tels que Windows les voit, et supprime par l'API d'appareils de Windows |

## Règles qui protègent les utilisateurs

1. **`iphone.py` reste en lecture seule.** Un test le garantit. N'ajoute jamais d'écriture ou de suppression AFC : ça contournerait la bibliothèque Photos.
2. **Dans le doute, la suppression refuse.** Si l'identité d'un fichier n'est pas prouvée, on ne touche à rien. Tout changement à cet endroit demande un test qui montre que le garde-fou refuse.
3. **Aucune donnée personnelle dans le dépôt.** Ni vraies photos, ni listes de fichiers, ni journaux, même dans les tests ou les captures.
4. **Aucune dépendance.** Uniquement la bibliothèque standard de Python, pour que l'installation reste triviale.
5. **Les deux langues, toujours.** Chaque texte de l'interface va dans `locales/en.json` et `locales/fr.json`. Un test vérifie qu'ils correspondent.

## Ajouter une langue

1. Copie `locales/en.json` vers `locales/xx.json` et traduis-le.
2. Ajoute `xx` à `LANGS` et au menu `#lang` dans `index.html`.
3. Ajoute le nom du dossier de sauvegarde à `DELETED_FOLDERS` et le mot de confirmation à `DELETE_WORDS` dans `rocket.py`.
