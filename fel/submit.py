import logging

from github import Github
from git import Repo, Commit

from meta import parse_meta, dump_meta
from rebase import tree_rebase


# This is a race condition because there is no way to create a PR without a
# branch. So we guess what the branch number should be, then try and create
# the PR ASAP so no one steals the PR number. If we get it wrong it doesn't
# matter because the commit is updated to have the actual PR afterwards
#
# Returns the ref that a commit stacked on top of this commit should base its PR
# on
def submit(repo, c, gh, upstream, username):
    logging.info("submitting %s to %s", c, upstream)

    # We can't handle merge commits
    assert len(c.parents) == 1

    # Don't submit commits that are already upstream
    if repo.is_ancestor(c, upstream):
        logging.info("skipping upstream commit %s", c)
        return upstream, {}

    # Make sure that our parent is already submitted
    base_ref, rebased = submit(repo, c.parents[0], gh, upstream, username)
    logging.info("pr base %s", base_ref)

    # If submitting c's parent rebased c, update c to what it was rebased to
    c = rebased.get(c, c)

    # Assert: At this point the parent commit's branch has been reset and force
    #         pushed to github

    # If the commit has been submitted before, grab the metadata and reset the
    # local head
    message, meta = parse_meta(c.message)
    try:
        pr_num = meta['fel-pr']
        diff_branch = repo.heads[meta['fel-branch']]

        # Reset the local branch and push to github
        logging.info("updating PR %s", pr_num)
        diff_branch.set_commit(c)
        repo.remote().push(diff_branch, force=True)

        # Update the base branch (This can happen when a stack gets rebased
        # after the bottom gets landed)
        pr = gh.get_pull(pr_num)
        pr.edit(base = base_ref.remote_head)

    except KeyError:
        logging.info("creating a PR")

        # Guess GitHub PR number
        pr_num = gh.get_pulls(state='all')[0].number + 1
        diff_branch = repo.create_head("fel/{}/{}".format(username, pr_num), commit=c)

        # Push branch to GitHub to create PR. 
        repo.remote().push(diff_branch)
        pr = gh.create_pull(title=c.summary, body="", head=diff_branch.name, base=base_ref.name)

        # Update the metadata
        meta['fel-pr'] = pr.number
        meta['fel-branch'] = diff_branch.name

        # Amend the commit with fel metadata and push to GitHub
        amended = c.replace(message = dump_meta(c.summary, meta))
        diff_branch.set_commit(amended)
        repo.remote().push(diff_branch, force=True)

        # Restack the commits on top
        rebased_commits = tree_rebase(repo, c, c, amended)

        # Update the rebased commits (every key points to the final rebased
        # commit, skipping all the intermediate commits)
        rebased = {k: rebased_commits.get(v, v) for k, v in rebased.items()}
        rebased.update(rebased_commits)

    return diff_branch, rebased