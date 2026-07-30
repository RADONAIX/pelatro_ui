// French translations for the application chrome.
//
// Keys are the exact English source strings passed to t(...). Anything not
// present here falls back to the English text. Domain/data labels (report
// titles, role names, assurance-scope names) are intentionally NOT translated.

export const FR: Record<string, string> = {
  // --- Root: not-found / error boundaries -----------------------------------
  "Page not found": "Page introuvable",
  "The page you're looking for doesn't exist or has been moved.":
    "La page que vous recherchez n'existe pas ou a été déplacée.",
  "Go home": "Retour à l'accueil",
  "This page didn't load": "Cette page n'a pas pu se charger",
  "Something went wrong on our end. You can try refreshing or head back home.":
    "Une erreur s'est produite de notre côté. Vous pouvez réessayer ou revenir à l'accueil.",
  "Try again": "Réessayer",

  // --- Sidebar --------------------------------------------------------------
  "Dashboard & KPIs": "Tableau de bord et KPI",
  "Reports & Certified Exports": "Rapports et exports certifiés",
  "Pipelines & Job Monitor": "Pipelines et suivi des tâches",
  "System Monitoring": "Supervision du système",
  MODULES: "MODULES",
  "Revenue Assurance": "Assurance des revenus",
  soon: "bientôt",
  "Not available yet": "Pas encore disponible",
  "Expand sidebar": "Déployer le menu latéral",
  "Collapse sidebar": "Réduire le menu latéral",
  Expand: "Déployer",
  Collapse: "Réduire",

  // --- Header ---------------------------------------------------------------
  "ASSURANCE SCOPE": "PÉRIMÈTRE D'ASSURANCE",
  "Toggle menu": "Afficher/masquer le menu",
  "Switch to light mode": "Passer en mode clair",
  "Switch to dark mode": "Passer en mode sombre",
  "Toggle dark mode": "Basculer le mode sombre",
  Settings: "Paramètres",
  "User Management": "Gestion des utilisateurs",
  "Role Management": "Gestion des rôles",
  "Audit Logs": "Journaux d'audit",
  Apply: "Appliquer",
  "e.g. email": "par ex. e-mail",
  "e.g. login, export": "par ex. connexion, export",
  "User and Action filter as you type; the date range reloads when you click Apply in the picker.":
    "Utilisateur et Action filtrent à la saisie ; la plage de dates se recharge lorsque vous cliquez sur Appliquer dans le sélecteur.",
  "No events match the current filters.": "Aucun événement ne correspond aux filtres actuels.",
  "Filter events by the user who performed them (matches email or name).":
    "Filtrez les événements par l'utilisateur qui les a effectués (correspond à l'e-mail ou au nom).",
  "Filter events by action type, e.g. login, export, or delete.":
    "Filtrez les événements par type d'action, par ex. connexion, export ou suppression.",
  "Show only events that occurred within the selected date range.":
    "Afficher uniquement les événements survenus dans la plage de dates sélectionnée.",
  Module: "Module",
  "All modules": "Tous les modules",
  "Filter events by the module they belong to, e.g. User Management or Bulk Exports.":
    "Filtrez les événements par le module auquel ils appartiennent, par ex. Gestion des utilisateurs ou Exports en masse.",
  // Audit module labels (backend rbac.AUDIT_MODULE_LABELS) not already listed above.
  Authentication: "Authentification",
  "Assurance Workbench": "Espace de travail d'assurance",
  "Case Management": "Gestion des cas",
  "Bulk Exports": "Exports en masse",
  "Sign out": "Se déconnecter",
  "Account menu": "Menu du compte",
  "View profile": "Voir le profil",
  User: "Utilisateur",
  Member: "Membre",

  // --- Profile --------------------------------------------------------------
  "User Profile": "Profil utilisateur",
  "Your account information and access.":
    "Les informations de votre compte et vos accès.",
  Email: "E-mail",
  Role: "Rôle",
  Department: "Service",
  "Last Login": "Dernière connexion",
  "Account Status": "Statut du compte",
  "Account Information": "Informations du compte",
  "Your account details and access.": "Vos informations de compte et vos accès.",
  "Full name is required.": "Le nom complet est requis.",
  "Full name must be at least 2 characters.": "Le nom complet doit comporter au moins 2 caractères.",
  "Full name must be at most 80 characters.": "Le nom complet doit comporter au plus 80 caractères.",
  "Full name can only contain letters, spaces, hyphens and apostrophes.":
    "Le nom complet ne peut contenir que des lettres, des espaces, des traits d'union et des apostrophes.",
  "Email is required.": "L'e-mail est requis.",
  "Enter a valid email address.": "Saisissez une adresse e-mail valide.",
  "Email must be at most 120 characters.": "L'e-mail doit comporter au plus 120 caractères.",
  "Enter a valid phone number.": "Saisissez un numéro de téléphone valide.",
  "Phone number must have 7 to 15 digits.": "Le numéro de téléphone doit comporter de 7 à 15 chiffres.",
  "Temporary password is required.": "Le mot de passe temporaire est requis.",
  "Password must be at least 8 characters.": "Le mot de passe doit comporter au moins 8 caractères.",
  "Password must be at most 72 characters.": "Le mot de passe doit comporter au plus 72 caractères.",
  "Password must include upper and lower case letters and a number.":
    "Le mot de passe doit contenir des lettres majuscules et minuscules et un chiffre.",
  "First name is required.": "Le prénom est requis.",
  "First name must be at least 2 characters.": "Le prénom doit comporter au moins 2 caractères.",
  "New password is required.": "Le nouveau mot de passe est requis.",
  "New password must be different from the current one.": "Le nouveau mot de passe doit être différent de l'actuel.",
  "Please confirm your new password.": "Veuillez confirmer votre nouveau mot de passe.",
  "Edit Profile": "Modifier le profil",
  "Change Password": "Changer le mot de passe",
  Language: "Langue",
  "Choose the language for the application interface.":
    "Choisissez la langue de l'interface de l'application.",

  // --- Dashboard ------------------------------------------------------------
  "Embedded Analytics — Superset Dashboard":
    "Analyse intégrée — Tableau de bord Superset",
  "Live Superset view embedded from the analytics platform.":
    "Vue Superset en direct intégrée depuis la plateforme d'analyse.",
  Refresh: "Actualiser",
  "Couldn't load the embedded dashboard":
    "Impossible de charger le tableau de bord intégré",
  "Open in Superset": "Ouvrir dans Superset",

  // --- Login ----------------------------------------------------------------
  "Welcome back": "Bon retour",
  "Sign in to your account": "Connectez-vous à votre compte",
  Password: "Mot de passe",
  "Sign in": "Se connecter",
  "Toggle password": "Afficher/masquer le mot de passe",
  "Secure revenue assurance": "Assurance des revenus sécurisée",
  "Login failed": "Échec de la connexion",
  or: "ou",
  "Continue with Google": "Continuer avec Google",
  "Continue with Microsoft": "Continuer avec Microsoft",
  "Coming soon": "Bientôt disponible",
  "This sign-in option isn't configured yet.":
    "Cette option de connexion n'est pas encore configurée.",

  // --- Access denied --------------------------------------------------------
  "Access Denied": "Accès refusé",
  "Your role doesn’t have permission to view this module. Contact your administrator if you need access.":
    "Votre rôle n'a pas l'autorisation de consulter ce module. Contactez votre administrateur si vous avez besoin d'un accès.",
  "Back to dashboard": "Retour au tableau de bord",

  // --- Monitoring -----------------------------------------------------------
  "Live system and application health, powered by Prometheus + Grafana — embedded below.":
    "Santé du système et de l'application en direct, propulsée par Prometheus + Grafana — intégrée ci-dessous.",
  "Open in Grafana": "Ouvrir dans Grafana",
  System: "Système",
  "API health": "Santé de l'API",
  "CPU, memory, disk and network for the application server.":
    "Processeur, mémoire, disque et réseau du serveur applicatif.",
  "Request rate, error rate and latency (p50/p95/p99) for the backend.":
    "Taux de requêtes, taux d'erreurs et latence (p50/p95/p99) du backend.",
  "Metrics are scraped by Prometheus every 15s from":
    "Les métriques sont collectées par Prometheus toutes les 15 s depuis",
  "(system) and the backend": "(système) et le backend",
  "endpoint (application). If the panel is blank, confirm Grafana is reachable at":
    "(application). Si le panneau est vide, vérifiez que Grafana est accessible à",

  // --- Audit logs -----------------------------------------------------------
  "Immutable trail of operator and system actions.":
    "Trace immuable des actions des opérateurs et du système.",
  "Event ID": "ID d'événement",
  Actor: "Acteur",
  Action: "Action",
  Target: "Cible",
  When: "Quand",

  // --- System configuration -------------------------------------------------
  "System Configuration": "Configuration du système",
  "Global runtime parameters for the assurance platform.":
    "Paramètres d'exécution globaux de la plateforme d'assurance.",
  "Saving…": "Enregistrement…",
  "Save changes": "Enregistrer les modifications",
  Environment: "Environnement",
  "Retention (days)": "Rétention (jours)",
  "SLA Window (minutes)": "Fenêtre SLA (minutes)",
  "Alert Email": "E-mail d'alerte",
  "Maintenance Mode": "Mode maintenance",
  Off: "Désactivé",
  On: "Activé",
  "Configuration saved": "Configuration enregistrée",
  "Failed to save configuration": "Échec de l'enregistrement de la configuration",

  // --- Report catalog (sidebar groups + report titles) ----------------------
  Files: "Fichiers",
  Reconciliation: "Réconciliation",
  Correlation: "Corrélation",
  Operations: "Opérations",
  "Raw Record Sequence Check Report":
    "Rapport de vérification de séquence des enregistrements bruts",
  "Processed Record Sequence Check Report":
    "Rapport de vérification de séquence des enregistrements traités",
  "SDP Raw Record Sequence Check Report":
    "Rapport de vérification de séquence des enregistrements bruts SDP",
  "File Sequence Check Report": "Rapport de vérification de séquence des fichiers",
  "File Exception Report": "Rapport des exceptions de fichiers",
  "AIR Reconciliation Report": "Rapport de réconciliation AIR",
  "SDP Reconciliation Report": "Rapport de réconciliation SDP",
  "MSC Reconciliation Report": "Rapport de réconciliation MSC",
  "Report Batch Log": "Journal des lots de rapports",

  // --- Reports --------------------------------------------------------------
  Reports: "Rapports",
  "Revenue Assurance reports — pick a report from the sidebar to drill down and export.":
    "Rapports d'assurance des revenus — choisissez un rapport dans le menu latéral pour explorer et exporter.",
  "Select a report": "Sélectionnez un rapport",
  "Live · 30s": "En direct · 30s",
  "Auto-refreshes every 30 seconds": "Actualisation automatique toutes les 30 secondes",
  "findings · showing up to 100": "résultats · affichage limité à 100",
  "Exporting…": "Exportation…",
  "Export CSV": "Exporter en CSV",
  "This report is not available yet.": "Ce rapport n'est pas encore disponible.",
  "Loading…": "Chargement…",
  "No rows match the current filters.": "Aucune ligne ne correspond aux filtres actuels.",
  "No data available for this report yet.":
    "Aucune donnée disponible pour ce rapport pour l'instant.",
  "Export failed": "Échec de l'exportation",

  // --- Report filters / pagination ------------------------------------------
  Filters: "Filtres",
  "Search all columns…": "Rechercher dans toutes les colonnes…",
  All: "Tous",
  "Date column to filter on": "Colonne de date à filtrer",
  "From date": "Date de début",
  "To date": "Date de fin",
  "Clear filters": "Effacer les filtres",
  "Rows per page": "Lignes par page",
  "First page": "Première page",
  "Previous page": "Page précédente",
  "Next page": "Page suivante",
  "Last page": "Dernière page",
  of: "sur",

  // --- Users ----------------------------------------------------------------
  "Manage operators, analysts and auditors who have access to RADONaix. Assign roles to control permissions.":
    "Gérez les opérateurs, analystes et auditeurs ayant accès à RADONaix. Attribuez des rôles pour contrôler les autorisations.",
  "Add a new user": "Ajouter un nouvel utilisateur",
  "Add user": "Ajouter un utilisateur",
  "Search by name or email": "Rechercher par nom ou e-mail",
  "All roles": "Tous les rôles",
  "All status": "Tous les statuts",
  Active: "Actif",
  Disabled: "Désactivé",
  Inactive: "Inactif",
  "Full Name": "Nom complet",
  Phone: "Téléphone",
  Status: "Statut",
  "No users match the current filters.":
    "Aucun utilisateur ne correspond aux filtres actuels.",
  "No data found": "Aucune donnée trouvée",
  "Edit user": "Modifier l'utilisateur",
  "Update user": "Mettre à jour l'utilisateur",
  "Create a new user account": "Créer un nouveau compte utilisateur",
  "Update user details and permissions": "Mettre à jour les informations et les autorisations de l'utilisateur",
  "Show password": "Afficher le mot de passe",
  "Hide password": "Masquer le mot de passe",
  "Reset password": "Réinitialiser le mot de passe",
  "Deactivate account": "Désactiver le compte",
  "Activate account": "Activer le compte",
  "User updated": "Utilisateur mis à jour",
  "User created": "Utilisateur créé",
  "Failed to save user": "Échec de l'enregistrement de l'utilisateur",
  "Account activated": "Compte activé",
  "Account deactivated": "Compte désactivé",
  "is now": "est désormais",
  active: "actif",
  disabled: "désactivé",
  "Failed to update status": "Échec de la mise à jour du statut",
  "Password reset sent to": "Réinitialisation du mot de passe envoyée à",
  "Confirm Deactivation": "Confirmer la désactivation",
  "Confirm Activation": "Confirmer l'activation",
  "This action will deactivate": "Cette action désactivera",
  "This action will activate": "Cette action activera",
  "The user will no longer be able to access the application.":
    "L'utilisateur ne pourra plus accéder à l'application.",
  "and allow access based on assigned permissions.":
    "et autorisera l'accès selon les autorisations attribuées.",
  "this account": "ce compte",
  "Confirm Deactivate": "Confirmer la désactivation",
  "Confirm Activate": "Confirmer l'activation",
  "Change status": "Modifier le statut",
  "Delete this user": "Supprimer cet utilisateur",
  "Delete user": "Supprimer l'utilisateur",
  "User deleted": "Utilisateur supprimé",
  "has been removed.": "a été supprimé.",
  "Failed to delete user": "Échec de la suppression de l'utilisateur",
  "Confirm Deletion": "Confirmer la suppression",
  "Confirm Delete": "Confirmer la suppression",
  "This action will permanently delete": "Cette action supprimera définitivement",
  "This cannot be undone.": "Cette action est irréversible.",
  "Full name": "Nom complet",
  "Assigned role": "Rôle attribué",
  "Temporary password": "Mot de passe temporaire",
  "min 8 chars": "min. 8 caractères",
  Cancel: "Annuler",
  "Save user": "Enregistrer l'utilisateur",

  // --- Roles ----------------------------------------------------------------
  "Create roles, configure page-level access, and control which modules each role can view or edit.":
    "Créez des rôles, configurez l'accès au niveau des pages et contrôlez les modules que chaque rôle peut consulter ou modifier.",
  "Create a new role": "Créer un nouveau rôle",
  "Select role": "Sélectionner le rôle",
  "New role": "Nouveau rôle",
  Roles: "Rôles",
  Updated: "Mis à jour le",
  "Edit role": "Modifier le rôle",
  "Permission matrix": "Matrice des autorisations",
  "Save permission matrix": "Enregistrer la matrice des autorisations",
  "Save permissions": "Enregistrer les autorisations",
  "Module / Page": "Module / Page",
  View: "Consulter",
  Edit: "Modifier",
  "view-only supported": "consultation seule prise en charge",
  "Checking": "Cocher",
  "automatically grants": "accorde automatiquement",
  "Unchecking": "Décocher",
  "removes": "retire",
  "and hides the module from the sidebar.":
    "et masque le module du menu latéral.",
  "Permissions saved": "Autorisations enregistrées",
  "Updated for": "Mis à jour pour",
  "Failed to save permissions": "Échec de l'enregistrement des autorisations",
  "Role saved": "Rôle enregistré",
  "Failed to save role": "Échec de l'enregistrement du rôle",
  "Role name": "Nom du rôle",
  Description: "Description",
  "Save role": "Enregistrer le rôle",
  "Update role": "Mettre à jour le rôle",
  "Update role details and status": "Mettre à jour les informations et le statut du rôle",
  "Role name is required.": "Le nom du rôle est requis.",
  "Role name must be at least 2 characters.": "Le nom du rôle doit comporter au moins 2 caractères.",
  "Role name must be at most 50 characters.": "Le nom du rôle doit comporter au plus 50 caractères.",
  "Role name can only contain letters, numbers, spaces and & / _ -":
    "Le nom du rôle ne peut contenir que des lettres, des chiffres, des espaces et & / _ -",
  "Description must be at most 200 characters.": "La description doit comporter au plus 200 caractères.",
  "A short, recognizable name for the role (e.g. RA Analyst). 2–50 characters.":
    "Un nom court et reconnaissable pour le rôle (par ex. Analyste RA). 2 à 50 caractères.",
  "Optional summary of what this role is for and who should have it (up to 200 characters).":
    "Résumé facultatif de l'objet de ce rôle et des personnes concernées (jusqu'à 200 caractères).",
  "Active roles can be assigned to users. Inactive roles are kept but cannot be assigned.":
    "Les rôles actifs peuvent être attribués aux utilisateurs. Les rôles inactifs sont conservés mais ne peuvent pas être attribués.",
  "The user's full name as it will appear across the app. Letters, spaces, hyphens and apostrophes only (2–80 characters).":
    "Le nom complet de l'utilisateur tel qu'il apparaîtra dans l'application. Lettres, espaces, traits d'union et apostrophes uniquement (2 à 80 caractères).",
  "Used to sign in and to receive account notifications. Must be a unique, valid email address.":
    "Utilisé pour se connecter et recevoir les notifications du compte. Doit être une adresse e-mail unique et valide.",
  "Optional contact number. Include the country code if relevant (7–15 digits).":
    "Numéro de contact facultatif. Incluez l'indicatif du pays le cas échéant (7 à 15 chiffres).",
  "Optional team or business unit the user belongs to, used for grouping and reporting.":
    "Équipe ou unité opérationnelle facultative à laquelle appartient l'utilisateur, utilisée pour le regroupement et les rapports.",
  "Determines what the user can view and edit. Permissions come from the role's configuration in Role Management.":
    "Détermine ce que l'utilisateur peut consulter et modifier. Les autorisations proviennent de la configuration du rôle dans la gestion des rôles.",
  "Active users can sign in and use the app. Disabled users are blocked from access until re-activated.":
    "Les utilisateurs actifs peuvent se connecter et utiliser l'application. Les utilisateurs désactivés n'ont pas accès jusqu'à leur réactivation.",
  "An initial password shared with the user. Must be 8+ characters with upper and lower case letters and a number. They'll be asked to change it on first sign-in.":
    "Un mot de passe initial communiqué à l'utilisateur. Doit comporter au moins 8 caractères avec des lettres majuscules et minuscules et un chiffre. Il devra le modifier à la première connexion.",
  "First name must be at most 40 characters.": "Le prénom doit comporter au plus 40 caractères.",
  "Name can only contain letters, spaces, hyphens and apostrophes.":
    "Le nom ne peut contenir que des lettres, des espaces, des traits d'union et des apostrophes.",
  "One lowercase letter": "Une lettre minuscule",
  "Your given name as shown across the app. Letters, spaces, hyphens and apostrophes only (2–40 characters).":
    "Votre prénom tel qu'il apparaît dans l'application. Lettres, espaces, traits d'union et apostrophes uniquement (2 à 40 caractères).",
  "Optional. Letters, spaces, hyphens and apostrophes only (up to 40 characters).":
    "Facultatif. Lettres, espaces, traits d'union et apostrophes uniquement (jusqu'à 40 caractères).",
  "Your sign-in email. This is managed by an administrator and cannot be changed here.":
    "Votre e-mail de connexion. Il est géré par un administrateur et ne peut pas être modifié ici.",
  "Optional team or business unit you belong to, used for grouping and reporting.":
    "Équipe ou unité opérationnelle facultative à laquelle vous appartenez, utilisée pour le regroupement et les rapports.",
  "Your existing password, needed to authorize this change.":
    "Votre mot de passe actuel, nécessaire pour autoriser cette modification.",
  "Must be 8–72 characters with upper and lower case letters and a number. Must differ from your current password.":
    "Doit comporter de 8 à 72 caractères avec des lettres majuscules et minuscules et un chiffre. Doit être différent de votre mot de passe actuel.",
  "Re-enter the new password exactly to confirm there are no typos.":
    "Saisissez à nouveau le nouveau mot de passe exactement pour confirmer l'absence de fautes de frappe.",
  Close: "Fermer",

  // --- Pipelines ------------------------------------------------------------
  Live: "En direct",
  "every": "toutes les",
  updated: "mis à jour",
  Export: "Exporter",
  "Failed to load pipeline data:": "Échec du chargement des données du pipeline :",
  Retry: "Réessayer",
  "Loading pipeline data…": "Chargement des données du pipeline…",
  "Window:": "Fenêtre :",
  last: "derniers",
  batches: "lots",
  "Active Work Pools · Running": "Pools de travail actifs · En cours",
  "No pipelines currently running.": "Aucun pipeline en cours d'exécution.",
  "Failed / partial / crashed": "Échoué / partiel / interrompu",
  "Hide batches with no files": "Masquer les lots sans fichiers",
  "Show batches with no files": "Afficher les lots sans fichiers",
  failed: "échoué",
  partial: "partiel",
  "no files": "sans fichiers",
  "with no files": "sans fichiers",
  "Source data quality observed across": "Qualité des données sources observée sur",
  "batches with zero-KB files": "lots avec des fichiers de 0 ko",
  "files total": "fichiers au total",
  "with corrupt files": "avec fichiers corrompus",
  "with decode errors": "avec erreurs de décodage",
  "with duplicates": "avec doublons",
  "These are upstream-source observations, not pipeline failures.": "Il s'agit d'observations de la source amont, non de défaillances du pipeline.",
  "Click to show this status": "Cliquez pour afficher ce statut",
  "Click to hide this status": "Cliquez pour masquer ce statut",
  "Export request submitted": "Demande d'export soumise",
  "Your file is being generated.": "Votre fichier est en cours de génération.",
  "Click here": "Cliquez ici",
  "to track it in the Download Center.": "pour le suivre dans le Centre de téléchargement.",
  "No issues in this window.": "Aucun problème sur cette période.",
  "Batch Status": "Statut des lots",
  "Flow Runs": "Exécutions de flux",
  "Batch durations (s) over the window": "Durées des lots (s) sur la période",
  total: "au total",
  "No batch data for the selected DAG / Stream.":
    "Aucune donnée de lot pour le DAG / flux sélectionné.",
  Failed: "Échoué",
  Partial: "Partiel",
  Success: "Réussi",
  Pending: "En attente",
  "Task Runs": "Exécutions de tâches",
  Completed: "Terminé",
  Events: "Événements",
  "AA Rows": "Lignes AA",
  "RR Rows": "Lignes RR",
  "Archived Files": "Fichiers archivés",
  Hour: "Heure",
  "Failed:": "Échoué :",
  "Partial:": "Partiel :",
  "Success:": "Réussi :",
  "Pending:": "En attente :",
  "No batches": "Aucun lot",
  Stream: "Flux",
  rows: "lignes",
  Watcher: "Surveillance",
  Decoder: "Décodeur",
  Ingestion: "Ingestion",
  Normalize: "Normalisation",
  Normalization: "Normalisation",
  Archived: "Archivés",
  Decoded: "Décodés",
  "Decode Failed": "Échec du décodage",
  Loaded: "Chargés",
  "Load Failed": "Échec du chargement",
  "Zero KB": "Zéro Ko",
  Duplicates: "Doublons",
  Corrupt: "Corrompus",
  Quarantined: "Mis en quarantaine",
  "Retried By": "Réessayé par",
  "Error:": "Erreur :",
  "Validation:": "Validation :",
  "Quarantine:": "Quarantaine :",
  Started: "Démarré",
  "files watched": "fichiers surveillés",
  complete: "terminé(s)",
  loaded: "chargé(s)",
  "rows normalized": "lignes normalisées",
  "Files watched": "Fichiers surveillés",
  Duplicate: "Doublon",
  Complete: "Terminé",
  Quarantine: "Quarantaine",
  Throughput: "Débit",
  "Average Latency": "Latence moyenne",
  "SLA Breaches": "Dépassements de SLA",
  "Failed in last 24h": "Échecs sur les dernières 24 h",

  // --- Export dialog --------------------------------------------------------
  "Export Batch Data": "Exporter les données de lots",
  "Filter the dataset, preview results, then download as CSV or drill into file-level logs.":
    "Filtrez le jeu de données, prévisualisez les résultats, puis téléchargez en CSV ou explorez les journaux au niveau des fichiers.",
  "DAG Type": "Type de DAG",
  "Stream Type": "Type de flux",
  From: "De",
  To: "À",
  "Search Batch ID": "Rechercher un ID de lot",
  "Reset filters": "Réinitialiser les filtres",
  Showing: "Affichage de",
  "records (filtered from": "enregistrements (filtrés sur",
  records: "enregistrements",
  "No records match the current filters.":
    "Aucun enregistrement ne correspond aux filtres actuels.",
  "View files": "Voir les fichiers",
  "Rows per page:": "Lignes par page :",
  Prev: "Préc.",
  Next: "Suiv.",
  Page: "Page",
  "Download CSV": "Télécharger en CSV",
  Back: "Retour",
  "File logs ·": "Journaux de fichiers ·",
  "Back to batches": "Retour aux lots",
  "File Status": "Statut du fichier",
  "File Type": "Type de fichier",
  "Search Filename": "Rechercher un nom de fichier",
  files: "fichiers",
  "No files match the current filters.":
    "Aucun fichier ne correspond aux filtres actuels.",
  "Loading files for": "Chargement des fichiers pour",
  "Failed to load files:": "Échec du chargement des fichiers :",
  Type: "Type",
  Start: "Début",
  End: "Fin",

  // --- MultiSelect ----------------------------------------------------------
  "Select…": "Sélectionner…",
  "Select all": "Tout sélectionner",
  Clear: "Effacer",
  selected: "sélectionné(s)",

  // --- Tooltip descriptions -------------------------------------------------
  "Open or close the navigation menu": "Ouvrir ou fermer le menu de navigation",
  "Change the assurance scope": "Changer le périmètre d'assurance",
  "Open the settings menu": "Ouvrir le menu des paramètres",
  "Sign out of your account": "Se déconnecter de votre compte",
  "Open the account menu": "Ouvrir le menu du compte",
  "Expand or collapse the reports list":
    "Développer ou réduire la liste des rapports",
  "Refresh the dashboard": "Actualiser le tableau de bord",
  "Show or hide the password": "Afficher ou masquer le mot de passe",
  "Save the configuration changes":
    "Enregistrer les modifications de configuration",
  "View this dashboard": "Afficher ce tableau de bord",
  "Download the report as CSV": "Télécharger le rapport au format CSV",
  "Show data for the last": "Afficher les données des derniers",
  "Export pipeline data": "Exporter les données du pipeline",
  "Retry loading the data": "Réessayer de charger les données",
  "Show batch details": "Afficher les détails du lot",
  "Hide batch details": "Masquer les détails du lot",
  "Show pipeline stages": "Afficher les étapes du pipeline",
  "Hide pipeline stages": "Masquer les étapes du pipeline",
  "Edit this user": "Modifier cet utilisateur",
  "Reset this user's password":
    "Réinitialiser le mot de passe de cet utilisateur",
  "Deactivate this account": "Désactiver ce compte",
  "Activate this account": "Activer ce compte",
  "Discard changes": "Annuler les modifications",
  "Save this user": "Enregistrer cet utilisateur",
  "Edit this role": "Modifier ce rôle",
  "View this role's permissions": "Afficher les autorisations de ce rôle",
  "Save the permission changes": "Enregistrer les modifications d'autorisations",
  "Save this role": "Enregistrer ce rôle",
  "Close this dialog": "Fermer cette fenêtre",
  "Confirm this action": "Confirmer cette action",
  "Cancel and close": "Annuler et fermer",
  "Select one or more options": "Sélectionner une ou plusieurs options",
  "Select all options": "Sélectionner toutes les options",
  "Clear the selection": "Effacer la sélection",
  "Reset all filters": "Réinitialiser tous les filtres",
  "View this batch's files": "Afficher les fichiers de ce lot",
  "Go to first page": "Aller à la première page",
  "Go to previous page": "Aller à la page précédente",
  "Go to next page": "Aller à la page suivante",
  "Go to last page": "Aller à la dernière page",
  "Download the data as CSV": "Télécharger les données au format CSV",
  "Go back": "Revenir en arrière",
  "Go back to the batch list": "Revenir à la liste des lots",
  "Edit your profile": "Modifier votre profil",
  "Change your password": "Changer votre mot de passe",
  "Expand the sidebar": "Développer le menu latéral",
  "Collapse the sidebar": "Réduire le menu latéral",

  // --- Page header info descriptions ----------------------------------------
  "View your account details and switch the application language.":
    "Consultez les détails de votre compte et changez la langue de l'application.",
  "Live Revenue Assurance KPIs and the embedded Superset analytics dashboard.":
    "Indicateurs d'assurance des revenus en direct et tableau de bord Superset intégré.",
  "Browse certified Revenue Assurance reports and export their findings.":
    "Parcourez les rapports certifiés d'assurance des revenus et exportez leurs résultats.",
  "Monitor processing pipelines, batch status and job health in real time.":
    "Surveillez les pipelines de traitement, l'état des lots et la santé des tâches en temps réel.",
  "System and application health metrics, powered by Prometheus and Grafana.":
    "Métriques de santé du système et de l'application, propulsées par Prometheus et Grafana.",
  "An immutable record of every operator and system action.":
    "Un enregistrement immuable de chaque action des opérateurs et du système.",
  "Global runtime settings for the assurance platform.":
    "Paramètres d'exécution globaux de la plateforme d'assurance.",
  "Create and manage users and assign their roles.":
    "Créez et gérez les utilisateurs et attribuez leurs rôles.",
  "Define roles and control each module's access permissions.":
    "Définissez les rôles et contrôlez les autorisations d'accès de chaque module.",

  // --- Report descriptions (info hint on the Reports page) ------------------
  "Detects gaps in the sequence of raw CDR records for each file and node. Each row flags a missing sequence range so dropped or lost records can be investigated.":
    "Détecte les ruptures dans la séquence des enregistrements CDR bruts pour chaque fichier et nœud. Chaque ligne signale une plage de séquence manquante afin d'enquêter sur les enregistrements perdus.",
  "Checks sequence continuity of processed records per file and node. Highlights missing sequence ranges introduced during processing so they can be traced.":
    "Vérifie la continuité de la séquence des enregistrements traités par fichier et par nœud. Met en évidence les plages de séquence manquantes apparues durant le traitement.",
  "Sequence-gap check for raw SDP records per file and node. Surfaces missing sequence ranges to catch records lost from the SDP source feed.":
    "Contrôle des ruptures de séquence pour les enregistrements SDP bruts par fichier et nœud. Fait ressortir les plages manquantes pour repérer les enregistrements perdus du flux SDP.",
  "Verifies that expected files arrive in sequence for each source and node. Flags missing or out-of-order files in the collection stream.":
    "Vérifie que les fichiers attendus arrivent dans l'ordre pour chaque source et nœud. Signale les fichiers manquants ou hors séquence dans le flux de collecte.",
  "Lists files that failed processing or arrived in an abnormal state. Use it to track rejected, corrupt or exception-flagged files per source.":
    "Liste les fichiers dont le traitement a échoué ou arrivés dans un état anormal. Permet de suivre les fichiers rejetés, corrompus ou signalés en exception par source.",
  "Reconciles raw versus processed AIR transactions and account balances per subscriber. Highlights amount or balance mismatches that may indicate revenue leakage.":
    "Réconcilie les transactions et soldes AIR bruts et traités par abonné. Met en évidence les écarts de montants ou de soldes pouvant indiquer une fuite de revenus.",
  "Reconciles SDP transactions between the source and processed data, surfacing discrepancies in amounts and balances. (Coming soon.)":
    "Réconcilie les transactions SDP entre les données source et traitées, en faisant ressortir les écarts de montants et de soldes. (Bientôt disponible.)",
  "Reconciles MSC call detail records between the source and processed data to detect mismatches. (Coming soon.)":
    "Réconcilie les enregistrements détaillés d'appels MSC entre les données source et traitées pour détecter les écarts. (Bientôt disponible.)",
  "Execution log of report-generation batches. Shows each run's process, start and end time, status and any error encountered.":
    "Journal d'exécution des lots de génération de rapports. Affiche pour chaque exécution le processus, les heures de début et de fin, le statut et toute erreur rencontrée.",

  // --- Filter descriptions (info hints on filter labels) --------------------
  "The source processing pipeline — AIR, MSC or SDP. Select one or more to filter the batches shown below.":
    "Le pipeline de traitement source — AIR, MSC ou SDP. Sélectionnez-en un ou plusieurs pour filtrer les lots affichés ci-dessous.",
  "The processing stage of the data — Raw (collected), Processed (decoded/loaded) or Reconciled. Choose which streams to include.":
    "L'étape de traitement des données — Brut (collecté), Traité (décodé/chargé) ou Réconcilié. Choisissez les flux à inclure.",
  "Filter batches by their source pipeline — AIR, MSC or SDP.":
    "Filtrer les lots par pipeline source — AIR, MSC ou SDP.",
  "Filter batches by processing stage — Raw, Processed or Reconciled.":
    "Filtrer les lots par étape de traitement — Brut, Traité ou Réconcilié.",
  "Filter batches by outcome — success, partial, failed, pending or running.":
    "Filtrer les lots par résultat — réussi, partiel, échoué, en attente ou en cours.",
  "Filter files by their processing outcome — success, failed, pending, running, duplicate or complete.":
    "Filtrer les fichiers par résultat de traitement — réussi, échoué, en attente, en cours, doublon ou terminé.",
  "Filter files by record type — AA, RR or CDR.":
    "Filtrer les fichiers par type d'enregistrement — AA, RR ou CDR.",
  "Narrow the rows below by text search, by a column's value, or by a date range. Filters apply to the rows currently loaded.":
    "Affinez les lignes ci-dessous par recherche de texte, par valeur de colonne ou par plage de dates. Les filtres s'appliquent aux lignes actuellement chargées.",

  // --- Status values (rendered via StatusBadge) -----------------------------
  SUCCESS: "RÉUSSI",
  FAILED: "ÉCHOUÉ",
  PARTIAL: "PARTIEL",
  PENDING: "EN ATTENTE",
  RUNNING: "EN COURS",
  IN_PROGRESS: "EN COURS",
  COMPLETE: "TERMINÉ",
  DUPLICATE: "DOUBLON",
  "In Progress": "En cours",
  Warning: "Avertissement",
  OK: "OK",

  // --- Report filter bar (labels, info hints, tooltips) ---------------------
  Search: "Recherche",
  "Search…": "Rechercher…",
  "Date range": "Plage de dates",
  "Reload this report": "Recharger ce rapport",
  "Search across every column in the rows currently loaded.":
    "Rechercher dans toutes les colonnes des lignes actuellement chargées.",
  "Show only rows that fall within the selected date range.":
    "N'afficher que les lignes comprises dans la plage de dates sélectionnée.",
  "Filter by data source — AIR or SDP.": "Filtrer par source de données — AIR ou SDP.",
  "Filter by stream — Raw or Processed.": "Filtrer par flux — Brut ou Traité.",
  "Filter rows by their status.": "Filtrer les lignes par statut.",
  "Filter rows by": "Filtrer les lignes par",

  // --- Report table column headers ------------------------------------------
  source: "source",
  stream: "flux",
  filename: "nom de fichier",
  node_id: "id nœud",
  file_node_id: "id nœud fichier",
  date: "date",
  status: "statut",
  missing_sequence_from: "séquence manquante (de)",
  missing_sequence_to: "séquence manquante (à)",
  missing_count: "nombre manquant",
  file_sequence: "séquence fichier",
  expected_file: "fichier attendu",
  batch_date: "date du lot",
  file_date: "date du fichier",
  record_date: "date d'enregistrement",
  file_status: "statut du fichier",
  // File Sequence Check — new columns
  file_type: "type de fichier",
  event_type: "type d'événement",
  sequence_cycle: "cycle de séquence",
  actual_file: "fichier réel",
  current_status: "statut actuel",
  probable_node_restart_flag: "redémarrage nœud probable",
  // Record Sequence Check — new columns
  previous_filename: "fichier précédent",
  next_filename: "fichier suivant",
  previous_sequence_number: "n° séquence précédent",
  next_sequence_number: "n° séquence suivant",
  previous_origin_timestamp: "horodatage origine précédent",
  next_origin_timestamp: "horodatage origine suivant",
  missing_sequence_count: "nombre de séquences manquantes",
  total_files_loaded: "total fichiers chargés",
  duplicate_file_count: "nombre de doublons",
  zero_kb_file_count: "nombre de fichiers 0 ko",
  corrupt_file_count: "nombre de fichiers corrompus",
  reconciliation_status: "statut de réconciliation",
  record_type: "type d'enregistrement",
  txn_id: "id transaction",
  subscriber_num: "n° d'abonné",
  raw_tran_amt: "montant transaction (brut)",
  proc_tran_amt: "montant transaction (traité)",
  raw_acc_balance: "solde compte (brut)",
  proc_acc_balance: "solde compte (traité)",
  created_time: "date de création",
  report_batch_id: "id lot rapport",
  process_name: "nom du processus",
  start_time: "heure de début",
  end_time: "heure de fin",
  error_message: "message d'erreur",

  // --- Report titles (the rest already exist above) -------------------------
  "Record Sequence Check": "Vérification de séquence des enregistrements",
  "File Sequence Check": "Vérification de séquence des fichiers",
  "File Summary Report": "Rapport de synthèse des fichiers",

  // --- Report descriptions (info-button text) -------------------------------
  "Sequence-gap check across AIR and SDP, raw and processed record streams. Each row flags a missing sequence range. Use the Source (AIR/SDP) and Stream (Raw/Processed) filters to narrow it down.":
    "Vérification des écarts de séquence sur AIR et SDP, flux d'enregistrements bruts et traités. Chaque ligne signale une plage de séquences manquante. Utilisez les filtres Source (AIR/SDP) et Flux (Brut/Traité) pour affiner.",
  "Verifies expected files arrive in sequence across AIR and SDP sources. Flags missing or out-of-order files. Filter by Source (AIR/SDP) and Stream (Raw/Processed).":
    "Vérifie que les fichiers attendus arrivent en séquence pour les sources AIR et SDP. Signale les fichiers manquants ou hors séquence. Filtrez par Source (AIR/SDP) et Flux (Brut/Traité).",
  "Lists files that failed processing or arrived in an abnormal state across AIR and SDP. Filter by Source (AIR/SDP) and Stream (Raw/Processed) to track rejected, corrupt or exception-flagged files.":
    "Liste les fichiers ayant échoué au traitement ou arrivés dans un état anormal sur AIR et SDP. Filtrez par Source (AIR/SDP) et Flux (Brut/Traité) pour suivre les fichiers rejetés, corrompus ou signalés en exception.",
  "Per-batch summary of AIR and SDP file processing — files loaded, plus duplicate, zero-KB and corrupt file counts. Filter by Source (AIR/SDP) and Stream (Raw/Processed).":
    "Synthèse par lot du traitement des fichiers AIR et SDP — fichiers chargés, ainsi que le nombre de doublons, de fichiers 0 ko et de fichiers corrompus. Filtrez par Source (AIR/SDP) et Flux (Brut/Traité).",
  "Reconciles raw versus processed SDP transactions and account balances per subscriber. Highlights amount or balance mismatches that may indicate revenue leakage.":
    "Réconcilie les transactions SDP brutes et traitées ainsi que les soldes de compte par abonné. Met en évidence les écarts de montant ou de solde pouvant indiquer une fuite de revenus.",
  "Execution log of AIR and SDP report-generation batches in one table — each run's process, start and end time, status and any error. Filter by Source (AIR/SDP).":
    "Journal d'exécution des lots de génération de rapports AIR et SDP dans un seul tableau — processus de chaque exécution, heures de début et de fin, statut et toute erreur. Filtrez par Source (AIR/SDP).",
};
