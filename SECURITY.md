# Security / Sécurité

**[English](#english) · [Français](#français)**

## English

### Reporting a vulnerability

Please do not open a public issue. Use GitHub's **Security → Report a vulnerability** on this repository.

### What protects your data

- **Nothing leaves your PC.** The tool has no network code besides a local server, which only listens on `127.0.0.1`.
- **Other websites cannot reach it.** The server checks the `Host` and `Sec-Fetch-Site` headers, and every action requires a custom header. This blocks DNS rebinding and cross-site requests.
- **Only your library is reachable.** The server serves files only if they are in the scanned library, never an arbitrary path.
- **The direct connection is read-only.** `iphone.py` implements no write or delete operation, and a test enforces it.
- **USB only.** The file protocol (AFC) is not encrypted, so devices connected over Wi-Fi are ignored.
- **Names from the phone are not trusted.** They are cleaned before becoming a path on your PC.

### What you should know

- **Private data on your disk.** Thumbnails and file lists are stored in `%LOCALAPPDATA%\iPhoneRocket`. Anyone with access to your Windows account can read them. Delete this folder to erase them.
- **The pairing key.** The tool reads your iPhone's pairing record from the Apple service. Python can only load this key from a file, so it stays in a temporary file for a few milliseconds during startup.
- **The helper.** `wpd-map.exe` is compiled on your PC from `wpd-map.cs` with the compiler built into Windows. It lists files and performs confirmed deletions. Some antivirus software may flag a newly compiled program.
- **Service start.** The "Start the service" button asks Windows for administrator approval to run `sc.exe start "Apple Mobile Device Service"`, and nothing else.

## Français

### Signaler une faille

N'ouvre pas d'issue publique. Utilise **Security → Report a vulnerability** sur ce dépôt.

### Ce qui protège tes données

- **Rien ne quitte ton PC.** L'outil n'a aucun code réseau à part un serveur local, qui écoute uniquement sur `127.0.0.1`.
- **Les autres sites ne peuvent pas l'atteindre.** Le serveur vérifie les en-têtes `Host` et `Sec-Fetch-Site`, et chaque action exige un en-tête personnalisé. Cela bloque le DNS rebinding et les requêtes intersites.
- **Seule ta bibliothèque est accessible.** Le serveur ne sert que les fichiers de la bibliothèque scannée, jamais un chemin arbitraire.
- **La connexion directe est en lecture seule.** `iphone.py` n'implémente aucune écriture ni suppression, et un test le garantit.
- **USB uniquement.** Le protocole de fichiers (AFC) n'est pas chiffré, donc les appareils connectés en Wi-Fi sont ignorés.
- **Les noms venant du téléphone ne sont pas considérés comme fiables.** Ils sont nettoyés avant de devenir un chemin sur ton PC.

### À savoir

- **Données privées sur ton disque.** Les miniatures et les listes de fichiers sont dans `%LOCALAPPDATA%\iPhoneRocket`. Toute personne ayant accès à ton compte Windows peut les lire. Supprime ce dossier pour les effacer.
- **La clé d'appairage.** L'outil lit l'appairage de ton iPhone auprès du service Apple. Python ne peut charger cette clé que depuis un fichier : elle reste donc dans un fichier temporaire quelques millisecondes au démarrage.
- **L'outil auxiliaire.** `wpd-map.exe` est compilé sur ton PC à partir de `wpd-map.cs`, avec le compilateur intégré à Windows. Il liste les fichiers et exécute les suppressions confirmées. Certains antivirus peuvent signaler un programme fraîchement compilé.
- **Démarrage du service.** Le bouton « Démarrer le service » demande à Windows l'accord administrateur pour lancer `sc.exe start "Apple Mobile Device Service"`, et rien d'autre.
