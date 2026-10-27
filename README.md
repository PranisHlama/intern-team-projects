# Odoo Team Projects

One shared repository with a separate branch for each group project.
The `main` branch contains only this README.

| Group project | Team branch | Project directory on that branch |
| --- | --- | --- |
| eosOne | `eosOne` | `eosOne/` |
| knowledge | `knowledge` | `knowledge/` |
| mail_client | `mail_client` | `mail_client/` |
| profit.co | `profit.co` | `profit.co/` |

Each team branch contains this guide and only its own project directory.
Switching branches changes which project directory is present in your checkout.
Commit or stash your work before switching branches.

## Start working

Once this repository has been published, clone it and switch to your team branch.
For example, for the knowledge team:

```sh
git clone <repository-url> odoo-team-projects
cd odoo-team-projects
git switch knowledge
git pull --ff-only origin knowledge
git switch -c knowledge/short-description
```

Use your team's branch name in place of `knowledge`. Put project files inside
that team's project directory. Use a unique feature branch name for each change.

```sh
git add knowledge/
git commit -m "Describe the change"
git push -u origin knowledge/short-description
```

Open a pull request with your team branch (for example, `knowledge`) as the base.
Keep project changes on their team branches so `main` stays README-only.
Before starting another change, switch to your team branch and pull its latest
changes, then create a new feature branch.

Keep passwords, tokens, and local `.env` files out of Git. Use `.env.example`
with placeholder values to document required configuration.

## Initial publishing

No remote is configured yet. After creating an empty remote repository, publish
all five branches:

```sh
git remote add origin <repository-url>
git push -u origin main eosOne knowledge mail_client profit.co
```

Set `main` as the default branch on the hosting service. Collaborators need write
access to the shared repository to push their feature branches.
