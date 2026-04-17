import os
import shutil

path = r"c:\Users\Shawn\Desktop\Session 4\INF5190\Projet Session\templates"
files = {
    'index.html': 'accueil.html',
    'login.html': 'connexion.html',
    'signup.html': 'inscription.html',
    'profile.html': 'profil.html',
    'search_results.html': 'resultats.html',
    'unsubscribe.html': 'desabonnement.html',
    'error.html': 'erreur.html'
}

for old, new in files.items():
    old_path = os.path.join(path, old)
    new_path = os.path.join(path, new)
    try:
        if os.path.exists(old_path):
            shutil.move(old_path, new_path)
            print(f'OK: {old} -> {new}')
        else:
            print(f'Fichier manquant: {old}')
    except Exception as e:
        print(f'Erreur: {e}')

print('Renommage complete!')
