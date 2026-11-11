"""Prospective logical-completeness prompt extension; preserves older pipelines."""
from pathlib import Path
import argparse,shutil,sys
from run import PACKAGE
from specmodel.io import write,sha,digest
from semantic_logic_review import INSTRUCTIONS,POLICY,PROMPT_POLICY

def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output',type=Path)
    args,_=parser.parse_known_args()
    if '--source-review'not in sys.argv and '--help'not in sys.argv:
        raise ValueError('Logical completion requires the source-review path')
    import source_review_prompt as prompt
    previous=prompt.SYSTEM,prompt.REVIEW,prompt.PROMPT_POLICY
    prompt.SYSTEM=previous[0]+'\n'+INSTRUCTIONS
    prompt.REVIEW=previous[1]+'\n'+INSTRUCTIONS
    prompt.PROMPT_POLICY=PROMPT_POLICY
    recipe={'policy':POLICY,'parent_prompt_policy':previous[2],
        'effective_prompt_policy':prompt.PROMPT_POLICY,
        'effective_system_sha256':digest(prompt.SYSTEM),
        'effective_review_sha256':digest(prompt.REVIEW),
        'no_private_answer_or_case_id_branches':True,
        'application':'Concatenate the generic extension to SYSTEM and REVIEW before actual model invocation.',
        'sources':[{'file':name,'sha256':sha(Path(__file__).with_name(name))}for name in
                   ['run_logical_completion.py','semantic_logic_review.py','source_review_prompt.py']]}
    try:
        from run_discovery import main as run
        run()
    finally:
        if args.output and args.output.is_dir():
            for item in recipe['sources']:
                source=Path(__file__).with_name(item['file'])
                if sha(source)!=item['sha256']:raise ValueError('Prompt source changed during execution')
                shutil.copyfile(source,args.output/'source_snapshot'/item['file'])
            write(args.output/'logical_review_extension.json',recipe)
        prompt.SYSTEM,prompt.REVIEW,prompt.PROMPT_POLICY=previous

if __name__=='__main__':main()
