# Prompt record

User task prompts for the construction of bcenv, in chronological order. Prompt text is preserved verbatim, including spelling and punctuation. Append new entries; never edit existing content. Entry headings and code fences are record metadata.

## Prompt 1

```text
The goal of bcenv is to build a battlecode environment for an AI agent to independently compete in the annual battlecode competition. Write a README.md and a VISION.md for this project, which will be licenced under GPLv3
```

## Prompt 2

```text
For the constructino of bcenv, we want a strict record of all prompts exactly as they were typed verbatim. Start PROMPTS.md to record all prompts so far, and use husky to ensure that all commits include and append-only addition to PROMPTS.md so that all commits will contain the prompt record.
```

## Prompt 3

```text
The VISION shoudl not stray into roadmap or immediate priorities. It is a VISION statement only.
```

## Prompt 4: Clarify prompt heading summaries

```text
The prompt record may contain a short 3-6 word summary of the prompt above the verbatim prompt. So instead of Prompt 4 then the pre block of this prompt, you might write: Prompt 4: Clarify headings and then show this prompt verbatim. This is not required, but may make the prompt record easier to scan later.
```

## Prompt 5: Publish repository and first PR

```text
Let's create the github repository and put this up as PR#1.
```

## Prompt 6: Prepare first pull request

Repeated from Prompt 5 for the project foundation commit supporting the same request.

```text
Let's create the github repository and put this up as PR#1.
```

## Prompt 7: Research history and sketch design

```text
OK that is rebased and merged. Let's start a new long-running research effort to try to summarize past approaches to battlecode and past approaches to usign AI to compete in battle code. Write HISTORICAL_LEARNINGS.md based upon all the examples you can find. Then write INITIAL_DESIGN_SKETCH to specify what you think the design of bcenv should be. Put up these draft documents as the next PR for review.
```

## Prompt 8: Expand research through my repositories

```text
review all of my repositories for battlecode related work and any repositories tehy refer to to expand your research.
```

## Prompt 9: Investigate Terry Van Belle

```text
did my repositories lead you to also look at Terry Van Belle's efforts in this area? If not, do so now.
```

## Prompt 10: Establish nested NixOS agent environments

```text
hmm. I feel like some high level description of containerization belongs near the top of the design sketch. I'm imagining a nixos VM image that can be deployed to a VM provider like GCE, which inside it uses nix to set up the tooling and environment for cloning the battlecode repository, and inside that does the battlecode development. The LLM doing the battlecode development should run inside that environment, but a second LLM is needed to create the VMs, set things up for the specific competition, and kick off and monitor the internal development loop. This outer LLM shoudl be able to supervise multiple competitors and evaluate their work and report back to the human about the outcomes; and it itself ideally would run in yet another container or VM that is isolated. I think Terry's setup is like this and even my past setups are more like this using either docker or nix to isolate concerns. let's update to include these ideas and make them prominent. we need a good frmework to work in to iterate on bcenv itself.
```

## Prompt 11: Build and validate cloud images

```text
OK I rebased and merged that. let's start a new branch, set up the cloud vm images, devise some sort of testing scheme to validate them when we change them, and put up a PR with the results.
```

## Prompt 12: Correct image validation harness

Repeated verbatim because this task requires a follow-up commit after CI validation.

```text
OK I rebased and merged that. let's start a new branch, set up the cloud vm images, devise some sort of testing scheme to validate them when we change them, and put up a PR with the results.
```

## Prompt 13: Handle SSH setting capitalization

Repeated verbatim for the follow-up commit correcting the VM assertion.

```text
OK I rebased and merged that. let's start a new branch, set up the cloud vm images, devise some sort of testing scheme to validate them when we change them, and put up a PR with the results.
```

## Prompt 14: Enable persistence test reboots

Repeated verbatim for the follow-up commit enabling reboots in the NixOS test driver.

```text
OK I rebased and merged that. let's start a new branch, set up the cloud vm images, devise some sort of testing scheme to validate them when we change them, and put up a PR with the results.
```
