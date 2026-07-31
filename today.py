import hashlib
import os

import requests
from lxml import etree

HEADERS = {"authorization": "token " + os.environ["ACCESS_TOKEN"]}
USER_NAME = os.environ["USER_NAME"]


def graphql(query, variables):
    r = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers=HEADERS,
    )

    if r.status_code != 200:
        raise Exception(f"GitHub API Error {r.status_code}\n{r.text}")

    data = r.json()

    if "errors" in data:
        raise Exception(data["errors"])

    return data


# --------------------------------------------------------
# Repository Counts
# --------------------------------------------------------

def repo_count(owner_affiliation):
    query = """
    query($login:String!,$owner:[RepositoryAffiliation]){
      user(login:$login){
        repositories(first:100,ownerAffiliations:$owner){
          totalCount
        }
      }
    }
    """

    data = graphql(
        query,
        {
            "login": USER_NAME,
            "owner": owner_affiliation,
        },
    )

    return data["data"]["user"]["repositories"]["totalCount"]


# --------------------------------------------------------
# Repository list (for LOC)
# --------------------------------------------------------

def repository_list(owner_affiliation, cursor=None, repos=None):
    if repos is None:
        repos = []

    query = """
    query($login:String!,$owner:[RepositoryAffiliation],$cursor:String){
      user(login:$login){
        repositories(
          first:60,
          after:$cursor,
          ownerAffiliations:$owner
        ){
          edges{
            node{
              nameWithOwner
              defaultBranchRef{
                target{
                  ... on Commit{
                    history{
                      totalCount
                    }
                  }
                }
              }
            }
          }

          pageInfo{
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    data = graphql(
        query,
        {
            "login": USER_NAME,
            "owner": owner_affiliation,
            "cursor": cursor,
        },
    )["data"]["user"]["repositories"]

    repos.extend(data["edges"])

    if data["pageInfo"]["hasNextPage"]:
        return repository_list(
            owner_affiliation,
            data["pageInfo"]["endCursor"],
            repos,
        )

    return repos


# --------------------------------------------------------
# Commit history of one repository
# --------------------------------------------------------

def repo_history(owner, repo, cursor=None,
                 add=0, delete=0, commits=0):

    query = """
    query($owner:String!,$repo:String!,$cursor:String){

      repository(owner:$owner,name:$repo){

        defaultBranchRef{

          target{

            ... on Commit{

              history(first:100,after:$cursor){

                edges{

                  node{

                    additions
                    deletions

                    author{
                      user{
                        login
                      }
                    }

                  }

                }

                pageInfo{
                  hasNextPage
                  endCursor
                }

              }

            }

          }

        }

      }

    }
    """

    data = graphql(
        query,
        {
            "owner": owner,
            "repo": repo,
            "cursor": cursor,
        },
    )

    branch = data["data"]["repository"]["defaultBranchRef"]

    if branch is None:
        return add, delete, commits

    history = branch["target"]["history"]

    for edge in history["edges"]:

        login = None

        if edge["node"]["author"]["user"] is not None:
            login = edge["node"]["author"]["user"]["login"]

        if login == USER_NAME:
            commits += 1
            add += edge["node"]["additions"]
            delete += edge["node"]["deletions"]

    if history["pageInfo"]["hasNextPage"]:

        return repo_history(
            owner,
            repo,
            history["pageInfo"]["endCursor"],
            add,
            delete,
            commits,
        )

    return add, delete, commits


# --------------------------------------------------------
# Cache
# --------------------------------------------------------

CACHE = (
    "cache/"
    + hashlib.sha256(USER_NAME.encode()).hexdigest()
    + ".txt"
)


def initialize_cache(edges):

    with open(CACHE, "w") as f:

        for edge in edges:

            repo = edge["node"]["nameWithOwner"]

            f.write(
                hashlib.sha256(repo.encode()).hexdigest()
                + " 0 0 0 0\n"
            )

def calculate_loc(edges):

    if not os.path.exists(CACHE):
        initialize_cache(edges)

    with open(CACHE) as f:
        cache = f.readlines()

    if len(cache) != len(edges):
        initialize_cache(edges)
        with open(CACHE) as f:
            cache = f.readlines()

    total_add = 0
    total_del = 0
    total_commits = 0

    new_cache = []

    for i, edge in enumerate(edges):

        repo = edge["node"]["nameWithOwner"]

        repo_hash = hashlib.sha256(repo.encode()).hexdigest()

        try:
            total_history = edge["node"]["defaultBranchRef"]["target"]["history"]["totalCount"]
        except:
            total_history = 0

        old = cache[i].split()

        if old[0] == repo_hash and int(old[1]) == total_history:

            commits = int(old[2])
            adds = int(old[3])
            dels = int(old[4])

        else:

            owner, repo_name = repo.split("/")

            adds, dels, commits = repo_history(owner, repo_name)

        new_cache.append(
            f"{repo_hash} {total_history} {commits} {adds} {dels}\n"
        )

        total_add += adds
        total_del += dels
        total_commits += commits

    with open(CACHE, "w") as f:
        f.writelines(new_cache)

    return (
        total_commits,
        total_add,
        total_del,
        total_add - total_del,
    )


# --------------------------------------------------------
# SVG
# --------------------------------------------------------

def replace(root, element_id, value):

    e = root.find(f".//*[@id='{element_id}']")

    if e is not None:
        e.text = str(value)


def update_svg(filename,
               repo_count_value,
               contrib_count,
               commit_count,
               loc_add,
               loc_del,
               loc_total):

    tree = etree.parse(filename)
    root = tree.getroot()

    replace(root, "repo_data", f"{repo_count_value:,}")
    replace(root, "contrib_data", f"{contrib_count:,}")
    replace(root, "commit_data", f"{commit_count:,}")

    replace(root, "loc_add", f"{loc_add:,}")
    replace(root, "loc_del", f"{loc_del:,}")
    replace(root, "loc_data", f"{loc_total:,}")

    # -------------------------------------------------
    # Fixed dots (never auto-justify)
    # -------------------------------------------------

    replace(root, "repo_data_dots", " .... ")
    replace(root, "commit_data_dots", " .......... ")
    replace(root, "loc_data_dots", ". ")
    replace(root, "loc_del_dots", " ")

    tree.write(
        filename,
        encoding="utf-8",
        xml_declaration=True,
    )


# --------------------------------------------------------
# Main
# --------------------------------------------------------

if __name__ == "__main__":

    OWNER = ["OWNER"]
    ALL = [
        "OWNER",
        "COLLABORATOR",
        "ORGANIZATION_MEMBER",
    ]

    print("Fetching repository list...")

    repos = repository_list(ALL)

    print("Calculating LOC...")

    commit_count, loc_add, loc_del, loc_total = calculate_loc(repos)

    repo_count_value = repo_count(OWNER)

    contrib_count = repo_count(ALL)

    update_svg(
        "dark_mode.svg",
        repo_count_value,
        contrib_count,
        commit_count,
        loc_add,
        loc_del,
        loc_total,
    )

    update_svg(
        "light_mode.svg",
        repo_count_value,
        contrib_count,
        commit_count,
        loc_add,
        loc_del,
        loc_total,
    )

    print()
    print("Done!")
    print(f"Repos        : {repo_count_value:,}")
    print(f"Contributed  : {contrib_count:,}")
    print(f"Commits      : {commit_count:,}")
    print(f"LOC          : {loc_total:,}")