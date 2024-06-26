#!/usr/bin/env python
# coding=utf-8
# Copyright 2021 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Fine-tuning the library models for sequence to sequence.
"""
# You can also adapt this script on your own sequence to sequence task. Pointers for this are left as comments.
import json 
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import nltk  # Here to have a nice missing dependency error message early on
import numpy as np
from datasets import load_dataset, load_metric

import transformers
from filelock import FileLock
from transformers import (
    AutoConfig,
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    HfArgumentParser,
    Seq2SeqTrainingArguments,
    set_seed,
)

from trainer_seq2seq import Seq2SeqTrainer # chenhui: use customized trainer to accept additional input arguments

from transformers.file_utils import is_offline_mode
from transformers.trainer_utils import get_last_checkpoint, is_main_process
from transformers.utils import check_min_version

from transformers.tokenization_utils_base import BatchEncoding

from modeling_bart import BartForConditionalGeneration

from tqdm import tqdm 
# Will error if the minimal version of Transformers is not installed. Remove at your own risks.
check_min_version("4.6.0.dev0")

try:
    nltk.data.find("tokenizers/punkt")
except (LookupError, OSError):
    if is_offline_mode():
        raise LookupError(
            "Offline mode: run this script without TRANSFORMERS_OFFLINE first to download nltk data files"
        )
    with FileLock(".lock") as lock:
        nltk.download("punkt", quiet=True)


@dataclass
class ModelArguments:
    """
    Arguments pertaining to which model/config/tokenizer we are going to fine-tune from.
    """

    model_name_or_path: str = field(
        metadata={"help": "Path to pretrained model or model identifier from huggingface.co/models"}
    )
    config_name: Optional[str] = field(
        default=None, metadata={"help": "Pretrained config name or path if not the same as model_name"}
    )
    tokenizer_name: Optional[str] = field(
        default=None, metadata={"help": "Pretrained tokenizer name or path if not the same as model_name"}
    )
    cache_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Where to store the pretrained models downloaded from huggingface.co"},
    )
    use_fast_tokenizer: bool = field(
        default=True,
        metadata={"help": "Whether to use one of the fast tokenizer (backed by the tokenizers library) or not."},
    )
    model_revision: str = field(
        default="main",
        metadata={"help": "The specific model version to use (can be a branch name, tag name or commit id)."},
    )
    use_auth_token: bool = field(
        default=False,
        metadata={
            "help": "Will use the token generated when running `transformers-cli login` (necessary to use this script "
            "with private models)."
        },
    )
    # chenhui: added args

    gen_target_min: int = field(
        default=20,
        metadata={
            "help": "min length to be generated by bart model"
        }
    )

    use_original_bart: bool = field(
        default=False,
        metadata={
            "help": "whether to use the unmodified original transformers bart architecture"
        }
    )

    no_posres_only: bool = field(
        default=False,
        metadata={
            "help": "whether to exclude position restart from enc_cross_doc"
        }
    )

    per_passage_source_length_limit: bool = field(
        default=False,
        metadata={
            "help": "whether to use max_source_length on the passage level (eg. if yes, and there are 3 passages, then total max source length is 3 * max_source_length)"
        }
    )

    print_processed_input: bool = field(
        default=False,
        metadata={
            "help": "whether to print the processed input text"
        }
    )

    doc_dec: bool = field(
        default=False,
        metadata={
            "help": "whether to attend to doc-level in decoder"
        }
    )

    enc_cross_doc: bool = field(
        default=False,
        metadata={
            "help": "whether to use cross to other doc embedding in encoder processing"
        }
    )

    eval_with_generate: bool = field(
        default=False,
        metadata={
            "help": "whether to generate for evaluation during training"
        }
    )

    model_analysis: bool = field(
        default=False,
        metadata={
            "help": "whether to return encoder decoder attention relative to documents"
        }
    )

    model_analysis_file: str = field(
        default=None,
        metadata={"help": "The specific file name to be saved in results/model_analysis/ if model_analysis flag is true."},
    )

    analyze_cross_attn: bool = field(
        default=False,
        metadata={
            "help": "whether to return decoder cross attention relative to documents"
        }
    )

    analyze_self_attn: bool = field(
        default=False,
        metadata={
            "help": "whether to return encoder self attention relative to documents"
        }
    )


@dataclass
class DataTrainingArguments:
    """
    Arguments pertaining to what data we are going to input our model for training and eval.
    """

    dataset_name: Optional[str] = field(
        default=None, metadata={"help": "The name of the dataset to use (via the datasets library)."}
    )
    dataset_config_name: Optional[str] = field(
        default=None, metadata={"help": "The configuration name of the dataset to use (via the datasets library)."}
    )
    text_column: Optional[str] = field(
        default=None,
        metadata={"help": "The name of the column in the datasets containing the full texts (for summarization)."},
    )
    summary_column: Optional[str] = field(
        default=None,
        metadata={"help": "The name of the column in the datasets containing the summaries (for summarization)."},
    )
    train_file: Optional[str] = field(
        default=None, metadata={"help": "The input training data file (a jsonlines or csv file)."}
    )
    validation_file: Optional[str] = field(
        default=None,
        metadata={
            "help": "An optional input evaluation data file to evaluate the metrics (rouge) on "
            "(a jsonlines or csv file)."
        },
    )
    test_file: Optional[str] = field(
        default=None,
        metadata={
            "help": "An optional input test data file to evaluate the metrics (rouge) on " "(a jsonlines or csv file)."
        },
    )
    overwrite_cache: bool = field(
        default=False, metadata={"help": "Overwrite the cached training and evaluation sets"}
    )
    preprocessing_num_workers: Optional[int] = field(
        default=None,
        metadata={"help": "The number of processes to use for the preprocessing."},
    )
    max_source_length: Optional[int] = field(
        default=2048,
        metadata={
            "help": "The maximum total input sequence length after tokenization. Sequences longer "
            "than this will be truncated, sequences shorter will be padded."
        },
    )
    max_target_length: Optional[int] = field(
        default=400,
        metadata={
            "help": "The maximum total sequence length for target text after tokenization. Sequences longer "
            "than this will be truncated, sequences shorter will be padded."
        },
    )
    val_max_target_length: Optional[int] = field(
        default=None,
        metadata={
            "help": "The maximum total sequence length for validation target text after tokenization. Sequences longer "
            "than this will be truncated, sequences shorter will be padded. Will default to `max_target_length`."
            "This argument is also used to override the ``max_length`` param of ``model.generate``, which is used "
            "during ``evaluate`` and ``predict``."
        },
    )
    pad_to_max_length: bool = field(
        default=False,
        metadata={
            "help": "Whether to pad all samples to model maximum sentence length. "
            "If False, will pad the samples dynamically when batching to the maximum length in the batch. More "
            "efficient on GPU but very bad for TPU."
        },
    )
    max_train_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of training examples to this "
            "value if set."
        },
    )
    max_eval_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of evaluation examples to this "
            "value if set."
        },
    )
    max_predict_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of prediction examples to this "
            "value if set."
        },
    )
    num_beams: Optional[int] = field(
        default=4,
        metadata={
            "help": "Number of beams to use for evaluation. This argument will be passed to ``model.generate``, "
            "which is used during ``evaluate`` and ``predict``."
        },
    )
    ignore_pad_token_for_loss: bool = field(
        default=True,
        metadata={
            "help": "Whether to ignore the tokens corresponding to padded labels in the loss computation or not."
        },
    )
    source_prefix: Optional[str] = field(
        default=None, metadata={"help": "A prefix to add before every source text (useful for T5 models)."}
    )

    def __post_init__(self):
        # chenhui: avoid error if only doing prediction
        if self.dataset_name is None and self.train_file is None and self.validation_file is None and self.test_file is None:
            raise ValueError("Need either a dataset name or a training/validation file.")
        else:
            if self.train_file is not None:
                extension = self.train_file.split(".")[-1]
                assert extension in ["csv", "json"], "`train_file` should be a csv or a json file."
            if self.validation_file is not None:
                extension = self.validation_file.split(".")[-1]
                assert extension in ["csv", "json"], "`validation_file` should be a csv or a json file."
        if self.val_max_target_length is None:
            self.val_max_target_length = self.max_target_length


summarization_name_mapping = {
    "amazon_reviews_multi": ("review_body", "review_title"),
    "big_patent": ("description", "abstract"),
    "cnn_dailymail": ("article", "highlights"),
    "orange_sum": ("text", "summary"),
    "pn_summary": ("article", "summary"),
    "psc": ("extract_text", "summary_text"),
    "samsum": ("dialogue", "summary"),
    "thaisum": ("body", "summary"),
    "xglue": ("news_body", "news_title"),
    "xsum": ("document", "summary"),
    "wiki_summary": ("article", "highlights"),
}


def main():
    # See all possible arguments in src/transformers/training_args.py
    # or by passing the --help flag to this script.
    # We now keep distinct sets of args, for a cleaner separation of concerns.

    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, Seq2SeqTrainingArguments))
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        model_args, data_args, training_args = parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
    else:
        model_args, data_args, training_args = parser.parse_args_into_dataclasses()
    
    logging.root.handlers = []
    if not os.path.exists("logs"):
        os.mkdir("logs")
    logger_file_path = "logs/"+training_args.output_dir.split('/')[-1]+"_logs.txt"
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(logger_file_path, mode="w", encoding="utf-8")],
    )

    logger = logging.getLogger(__name__) 
    logger.addHandler(logging.StreamHandler())

    logger.setLevel(logging.INFO if is_main_process(training_args.local_rank) else logging.WARN)

    if data_args.source_prefix is None and model_args.model_name_or_path in [
        "t5-small",
        "t5-base",
        "t5-large",
        "t5-3b",
        "t5-11b",
    ]:
        logger.warning(
            "You're running a t5 model but didn't provide a source prefix, which is the expected, e.g. with "
            "`--source_prefix 'summarize: ' `"
        )

    # Detecting last checkpoint.
    last_checkpoint = None
    if os.path.isdir(training_args.output_dir) and training_args.do_train and not training_args.overwrite_output_dir:
        last_checkpoint = get_last_checkpoint(training_args.output_dir)
        if last_checkpoint is None and len(os.listdir(training_args.output_dir)) > 0:
            raise ValueError(
                f"Output directory ({training_args.output_dir}) already exists and is not empty. "
                "Use --overwrite_output_dir to overcome."
            )
        elif last_checkpoint is not None and training_args.resume_from_checkpoint is None:
            logger.info(
                f"Checkpoint detected, resuming training at {last_checkpoint}. To avoid this behavior, change "
                "the `--output_dir` or add `--overwrite_output_dir` to train from scratch."
            )


    # Log on each process the small summary:
    logger.warning(
        f"Process rank: {training_args.local_rank}, device: {training_args.device}, n_gpu: {training_args.n_gpu}"
        + f"distributed training: {bool(training_args.local_rank != -1)}, 16-bits training: {training_args.fp16}"
    )
    # Set the verbosity to info of the Transformers logger (on main process only):
    if is_main_process(training_args.local_rank):
        transformers.utils.logging.set_verbosity_info()
    logger.info(f"Training/evaluation parameters {training_args}")

    # Set seed before initializing model.
    set_seed(training_args.seed)

    # if use_dataloader is False:
    if data_args.dataset_name is not None:
        # Downloading and loading a dataset from the hub.
        datasets = load_dataset(data_args.dataset_name, data_args.dataset_config_name, cache_dir=model_args.cache_dir)
    else:
        data_files = {}
        if data_args.train_file is not None:
            data_files["train"] = data_args.train_file
            extension = data_args.train_file.split(".")[-1]
        if data_args.validation_file is not None:
            data_files["validation"] = data_args.validation_file
            extension = data_args.validation_file.split(".")[-1]
        if data_args.test_file is not None:
            data_files["test"] = data_args.test_file
            extension = data_args.test_file.split(".")[-1]
        datasets = load_dataset(extension, data_files=data_files, cache_dir=model_args.cache_dir)


    # Load pretrained model and tokenizer
    config = AutoConfig.from_pretrained(
        model_args.config_name if model_args.config_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
        padding=True,  # Add padding
        truncation=True  
    )

    # chenhui: allow source length > 1024
    if data_args.max_source_length > 1024:
        logger.info(f"setting max position embedding: {data_args.max_source_length}")
        config.max_position_embeddings = data_args.max_source_length
    config.max_length = data_args.max_target_length
    config.min_length = model_args.gen_target_min
    
    # chenhui: add to config
    config.use_original_bart = model_args.use_original_bart 
    config.enc_cross_doc = model_args.enc_cross_doc
    config.doc_dec = model_args.doc_dec if model_args.enc_cross_doc else False # chenhui: doc_dec dependent on enc_cross_dec
    # NOTE: no_posres_only is valid only if enc_cross_doc is true
    config.no_posres_only = model_args.no_posres_only if model_args.enc_cross_doc else False

    tokenizer = AutoTokenizer.from_pretrained(
        model_args.tokenizer_name if model_args.tokenizer_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        use_fast=model_args.use_fast_tokenizer,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
    )

    model = BartForConditionalGeneration.from_pretrained(
        model_args.model_name_or_path,
        from_tf=bool(".ckpt" in model_args.model_name_or_path),
        config=config,
        cache_dir=model_args.cache_dir,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
    )
    # chenhui: select part of the architecture to use
    # NOTE: esp. for the encoder side control
    # need to update accordingly in generation_utils.py, _prepare_encoder_decoder_kwargs_for_generation!
    model.enc_cross_doc = model_args.enc_cross_doc 
    model.doc_dec = model_args.doc_dec

    
    model.resize_token_embeddings(len(tokenizer))

    if model.config.decoder_start_token_id is None:
        raise ValueError("Make sure that `config.decoder_start_token_id` is correctly defined")


    prefix = data_args.source_prefix if data_args.source_prefix is not None else ""

    if training_args.do_train:
        column_names = datasets["train"].column_names
    elif training_args.do_eval:
        column_names = datasets["validation"].column_names
    elif training_args.do_predict:
        column_names = datasets["test"].column_names
    else:
        logger.info("There is nothing to do. Please pass `do_train`, `do_eval` and/or `do_predict`.")
        return
    # Get the column names for input/target.
    dataset_columns = summarization_name_mapping.get(data_args.dataset_name, None)
    if data_args.text_column is None:
        text_column = dataset_columns[0] if dataset_columns is not None else column_names[-2]
    else:
        text_column = data_args.text_column
        if text_column not in column_names:
            raise ValueError(
                f"--text_column' value '{data_args.text_column}' needs to be one of: {', '.join(column_names)}"
            )
    if data_args.summary_column is None:
        summary_column = dataset_columns[1] if dataset_columns is not None else column_names[-1]
    else:
        summary_column = data_args.summary_column
        if summary_column not in column_names:
            raise ValueError(
                f"--summary_column' value '{data_args.summary_column}' needs to be one of: {', '.join(column_names)}"
            )
        
    # Temporarily set max_target_length for training.
    max_target_length = data_args.max_target_length
    padding = "max_length" if data_args.pad_to_max_length else False

    if training_args.label_smoothing_factor > 0 and not hasattr(model, "prepare_decoder_input_ids_from_labels"):
        logger.warning(
            "label_smoothing is enabled but the `prepare_decoder_input_ids_from_labels` method is not defined for"
            f"`{model.__class__.__name__}`. This will lead to loss being calculated twice and will take up more memory"
        )

    def preprocess_function(examples):
        inputs = examples[text_column]
        targets = examples[summary_column]
        # DEBUG
        try:
            inputs = [prefix + inp for inp in inputs]
        except:
            print("prefix:", prefix)
            print([inp for inpt in inputs if inp is None])

        if model_args.use_original_bart: # chenhui: the original preprocess function
            model_inputs = tokenizer(inputs, max_length=data_args.max_source_length, padding=padding, truncation=True)
            with tokenizer.as_target_tokenizer():
                labels = tokenizer(targets, max_length=max_target_length, padding=padding, truncation=True)

            # If we are padding here, replace all tokenizer.pad_token_id in the labels by -100 when we want to ignore
            # padding in the loss.
            if padding == "max_length" and data_args.ignore_pad_token_for_loss:
                labels["input_ids"] = [
                    [(l if l != tokenizer.pad_token_id else -100) for l in label] for label in labels["input_ids"]
                ]
            model_inputs["labels"] = labels["input_ids"]
            return model_inputs  

        # chenhui: adding <s> in front of each document
        input_ids_list = []
        labels_list = []
        # indicates the start position for control, rating_sent, rev1, rev2, .., last rev
        # NOTE: this is also the bos positions
        sep_positions_list = [] 
        doc_token = tokenizer.bos_token_id

        for (source, target) in zip(inputs, targets):
            reviews = source.split(" <REVBREAK> ")
            reviews = [x for x in reviews if x.strip()!=""] # remove empty ones
            if model_args.per_passage_source_length_limit:
                psg_len_limit = (data_args.max_source_length - len(reviews)) // len(reviews)

            input_ids = []
            sep_positions = [0]

            for source in reviews:
                rev_ids = tokenizer.encode(source, padding=padding)[1:-1]
                if model_args.per_passage_source_length_limit:
                    rev_ids = rev_ids[:psg_len_limit]
                input_ids.extend([doc_token]+rev_ids) # NOTE: for no </s> btw passages
                sep_positions.append(len(input_ids))
                    
            sep_positions = sep_positions[:-1] # no need to append to sep_positions_list for last one, which is not the starting pos of new passage
            input_ids.append(tokenizer.eos_token_id) # NOTE: for no </s> btw passages
            
            if len(input_ids) > data_args.max_source_length: 
                input_ids = input_ids[:data_args.max_source_length-1]+[tokenizer.eos_token_id]
                sep_positions = [x for x in sep_positions if x < data_args.max_source_length-1] # remove pos of those passage that exceed max source length
                
            with tokenizer.as_target_tokenizer():
                decoder_token_ids = tokenizer.encode(target,  max_length=max_target_length, padding=padding, truncation=True)

            if padding == "max_length" and data_args.ignore_pad_token_for_loss:
                decoder_token_ids = [
                    label if label != tokenizer.pad_token_id else -100 for label in decoder_token_ids
                ]

            if len(sep_positions) == 0: # chenhui: some datasets contains empty src, just filter
                print("no source!")
                print("original source:", source)
                print("original target:", target)
                continue

            input_ids_list.append(input_ids) 
            sep_positions_list.append(sep_positions)
            labels_list.append(decoder_token_ids)


        def pad_customized_list(input_list):
            if len(input_list) == 0 or len(input_list[0])==0:
                return input_list
            max_len = max([len(x) for x in input_list])
            padded = [item+[-1]*(max_len-len(item)) for item in input_list]
            return padded

        padded_sep_positions_list = pad_customized_list(sep_positions_list)

        # NOTE: IMPORTANT decoder_input_ids is already the target, don't use the same name!
        data = {
            "input_ids": input_ids_list,
            "labels":labels_list,
            "sep_positions": padded_sep_positions_list,
        }

        if model_args.print_processed_input:
            item = input_ids_list[0]
            sep = sep_positions_list[0]
            rev_pieces = []
            for i in range(len(sep)-1):
                start = sep[i]
                end = sep[i+1]
                rev = item[start:end]
                rev_pieces.append(tokenizer.decode(rev).encode('utf-8'))
            rev = item[end:]
            rev_pieces.append(tokenizer.decode(rev).encode('utf-8'))
            gold = labels_list[0]
            gold_text = tokenizer.decode(gold).encode('utf-8')
            logger.info("reviews:")
            for rev in rev_pieces:
                logger.info(f"{rev}\n")
            logger.info(f"meta-review:\n{gold_text}\n\n")

        model_inputs = BatchEncoding(data=data)
        return model_inputs


    if training_args.do_train:
        train_dataset = datasets["train"]
        if "train" not in datasets:
            raise ValueError("--do_train requires a train dataset")
        if data_args.max_train_samples is not None:
            train_dataset = train_dataset.select(range(data_args.max_train_samples))
        train_dataset = train_dataset.map(
            preprocess_function,
            batched=True,
            num_proc=data_args.preprocessing_num_workers,
            remove_columns=column_names,
            load_from_cache_file=False
        )
    if training_args.do_eval:
        max_target_length = data_args.val_max_target_length
        if "validation" not in datasets:
            raise ValueError("--do_eval requires a validation dataset")
        eval_dataset = datasets["validation"]
        if data_args.max_eval_samples is not None:
            eval_dataset = eval_dataset.select(range(data_args.max_eval_samples))
        eval_dataset = eval_dataset.map(
            preprocess_function,
            batched=True,
            num_proc=data_args.preprocessing_num_workers,
            remove_columns=column_names,
            load_from_cache_file=False,
        )
    if training_args.do_predict:
        max_target_length = data_args.val_max_target_length
        if "test" not in datasets:
            raise ValueError("--do_predict requires a test dataset")
        predict_dataset = datasets["test"]
        if data_args.max_predict_samples is not None:
            predict_dataset = predict_dataset.select(range(data_args.max_predict_samples))
        predict_dataset = predict_dataset.map(
            preprocess_function,
            batched=True,
            num_proc=data_args.preprocessing_num_workers,
            remove_columns=column_names,
            load_from_cache_file=False,
        )

    # Data collator
    label_pad_token_id = -100 if data_args.ignore_pad_token_for_loss else tokenizer.pad_token_id
    data_collator = DataCollatorForSeq2Seq(
        tokenizer,
        model=model,
        label_pad_token_id=label_pad_token_id,
        pad_to_multiple_of=8 if training_args.fp16 else None,
    )

    # Metric
    metric = load_metric("rouge")

    def remove_prefix(text):
        prefix_pos = text.find(" ==> ")
        if prefix_pos >= 0:
            return text[prefix_pos+len(" ==> "):]
        else:
            return text

    def postprocess_text(preds, labels):
        preds = [pred.strip() for pred in preds]
        labels = [label.strip() for label in labels]

        # rougeLSum expects newline after each sentence
        preds = ["\n".join(nltk.sent_tokenize(pred)) for pred in preds]
        labels = ["\n".join(nltk.sent_tokenize(label)) for label in labels]

        return preds, labels

    def compute_metrics(eval_preds):
        preds, labels = eval_preds
        if isinstance(preds, tuple):
            preds = preds[0]
        decoded_preds = tokenizer.batch_decode(preds, skip_special_tokens=True)
        if data_args.ignore_pad_token_for_loss:
            # Replace -100 in the labels as we can't decode them.
            labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
        decoded_labels = tokenizer.batch_decode(labels, skip_special_tokens=True)

        # Some simple post-processing
        decoded_preds, decoded_labels = postprocess_text(decoded_preds, decoded_labels)

        result = metric.compute(predictions=decoded_preds, references=decoded_labels, use_stemmer=True)
        # Extract a few results from ROUGE
        result = {key: value.mid.fmeasure * 100 for key, value in result.items()}

        prediction_lens = [np.count_nonzero(pred != tokenizer.pad_token_id) for pred in preds]
        result["gen_len"] = np.mean(prediction_lens)
        result = {k: round(v, 4) for k, v in result.items()}
        return result

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset if training_args.do_train else None,
        eval_dataset=eval_dataset if training_args.do_eval else None,
        tokenizer=tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics if model_args.eval_with_generate else None,
    )

    # Training
    if training_args.do_train:
        checkpoint = None
        if training_args.resume_from_checkpoint is not None:
            checkpoint = training_args.resume_from_checkpoint
        elif last_checkpoint is not None:
            checkpoint = last_checkpoint
        train_result = trainer.train(resume_from_checkpoint=checkpoint)
        trainer.save_model()  # Saves the tokenizer too for easy upload

        metrics = train_result.metrics
        max_train_samples = (
            data_args.max_train_samples if data_args.max_train_samples is not None else len(train_dataset)
        )
        metrics["train_samples"] = min(max_train_samples, len(train_dataset))

        trainer.log_metrics("train", metrics)
        trainer.save_metrics("train", metrics)
        trainer.save_state()
        # chenhui: mannually save the modified config file 
        # NOTE: need to have a config file in order to load from pre-trained
        config.save_pretrained(training_args.output_dir)


    # Evaluation
    results = {}
    if training_args.do_eval:
        logger.info("*** Evaluate ***")

        # chenhui: add compute_metrics 
        if training_args.predict_with_generate:
            trainer.compute_metrics = compute_metrics

        metrics = trainer.evaluate(
            max_length=data_args.val_max_target_length, num_beams=data_args.num_beams, metric_key_prefix="eval"
        )
        max_eval_samples = data_args.max_eval_samples if data_args.max_eval_samples is not None else len(eval_dataset)
        metrics["eval_samples"] = min(max_eval_samples, len(eval_dataset))

        trainer.log_metrics("eval", metrics)
        trainer.save_metrics("eval", metrics)

    if training_args.do_predict:
        logger.info("*** Predict ***")

        ### simplified faster test
        test_dataloader = trainer.get_test_dataloader(predict_dataset)
        trainer.model = trainer._wrap_model(trainer.model, training=False)
        trainer.model.eval()
        trainer.callback_handler.eval_dataloader = test_dataloader
        eval_dataset = test_dataloader.dataset

        decoded_preds = []
        decoded_labels = []
        prediction_file = os.path.join(training_args.output_dir, "pred.txt")
        prediction_result_file = os.path.join(training_args.output_dir, "result.json")
        
        if model_args.model_analysis:
            cross_attn_analysis_list = []
            self_attn_analysis_list = []
            self_doc_attn_analysis_list = []
            if not model_args.model_analysis_file:
                raise Exception("--model_analysis_file must have a str in order to save the model analysis results!")

            model_analysis_file_path = os.path.join(training_args.output_dir, model_args.model_analysis_file)

        with open(prediction_file, "w", encoding='utf-8') as writer:
            for step, inputs in enumerate(tqdm(test_dataloader)):
                # to avoid error: out of range integral type conversion attempted
                gold_id = np.where(inputs['labels'] != -100, inputs['labels'], tokenizer.pad_token_id)
                gold_text = tokenizer.batch_decode(gold_id,skip_special_tokens=True, clean_up_tokenization_spaces=True)[0].strip()
                decoded_labels.append(gold_text)

                inputs = trainer._prepare_inputs(inputs)

                gen_kwargs = {
                    "max_length": data_args.max_target_length,
                    "num_beams": data_args.num_beams,
                    "synced_gpus": False,
                    "output_attentions": model_args.model_analysis, # model analsys
                    "return_dict_in_generate": model_args.model_analysis, # model analsys
                    "output_scores": model_args.model_analysis
                }


                if hasattr(trainer.model, "encoder") and trainer.model.encoder.main_input_name != trainer.model.main_input_name:
                    generation_inputs = inputs[trainer.model.encoder.main_input_name]
                else:
                    generation_inputs = inputs[trainer.model.main_input_name]

                if model_args.use_original_bart:
                    output = trainer.model.generate(
                        generation_inputs,
                        attention_mask=inputs.get("attention_mask", None),
                        **gen_kwargs,
                    ) 
                else:
                    output = trainer.model.generate(
                        generation_inputs,
                        sep_positions = inputs.get("sep_positions", None), # add new arg
                        decoder_prefix_ids = inputs.get("decoder_prefix_ids", None), # add new arg
                        attention_mask=inputs.get("attention_mask", None),
                        **gen_kwargs,
                    ) 

                if model_args.model_analysis:
                    import torch 
                    # inputs keys:" ['input_ids', 'labels', 'sep_positions', 'attention_mask', 'decoder_input_ids']
                    sep_positions = inputs['sep_positions'][0].tolist()
                    src_len = inputs["input_ids"].size(-1)

                    generated_tokens = output.sequences 

                    if model_args.analyze_cross_attn:
                        gen_len = generated_tokens.size(-1)
                        cross_attentions =  output.cross_attentions
                        avg_cross_attention = [sum(x)/len(x) for x in cross_attentions]
                        selected_cross_attention = []
                        beam_indices = output.beam_indices[0] # since single batch
                       
                        # chenhui: beam_indics initially same length as cross attention, after finalize in the BeamSearchScorer, it gets shorter, same is gen_len
                        for idx, cross_attn in zip(beam_indices, avg_cross_attention[:len(beam_indices)]):
                            # cross_attn of shape (num_beams, num_heads, 1, src_len)
                            token_cross_attn = torch.mean(cross_attn[idx], dim = 0) # reduce [num_heads, src_len] --> [1, src_len]
                            selected_cross_attention.append(token_cross_attn)
                        processed_cross_attn = torch.cat(selected_cross_attention, dim=0) # [gen_len, src_len]

                        doc_cross_attn_list = []
                        for start, end in zip(sep_positions, sep_positions[1:]+[src_len]): # TODO: need to debug!!!
                            doc_cross_attn = torch.sum(processed_cross_attn[:, start:end], dim=-1) # [gen_len]
                            doc_cross_attn_list.append(doc_cross_attn.unsqueeze(dim=-1))
                        doc_cross_attn = torch.cat(doc_cross_attn_list, dim=-1) # [gen_len, num_docs]
                        doc_softmax = torch.nn.Softmax(dim=-1)
                        doc_cross_attn = doc_softmax(doc_cross_attn) # [gen_len, num_docs] 
                        cross_attn_to_doc = torch.mean(torch.std(doc_cross_attn, unbiased=False, dim=-1))
                        cross_attn_analysis_list.append(cross_attn_to_doc.item())

                    if model_args.analyze_self_attn:
                        encoder_attentions = output.encoder_attentions
                        avg_self_attention = sum(encoder_attentions) / len(encoder_attentions) # size [bsz, num_heads, src_len, src_len]
                        avg_self_attention = torch.mean(avg_self_attention, dim=1) # size [bsz, src_len, src_len]

                        doc_token_attn = avg_self_attention[:, sep_positions, :]
                        doc_self_attn_list = []
                        for start, end in zip(sep_positions, sep_positions[1:]+[src_len]):
                            doc_self_attn = torch.sum(doc_token_attn[:, :, start:end], dim=-1).unsqueeze(dim=-1)
                            doc_self_attn_list.append(doc_self_attn)
                        
                        doc_self_attn = torch.cat(doc_self_attn_list, dim=-1).view(-1, len(sep_positions)) # [num_docs, num_docs]

                        # see on avg how much attention on the self doc
                        self_doc_attn_weights = torch.masked_select(doc_self_attn, torch.eye(doc_self_attn.size(-1)).bool().to(doc_self_attn.device))
                        self_doc_attn_weights = torch.mean(self_doc_attn_weights).item()
                        self_doc_attn_analysis_list.append(self_doc_attn_weights)

                        doc_softmax = torch.nn.Softmax(dim=-1)
                        doc_self_attn = doc_softmax(doc_self_attn) # [num_docs, num_docs] 
                        self_attn_to_doc = torch.mean(torch.std(doc_self_attn, unbiased=False, dim=-1))
                        self_attn_analysis_list.append(self_attn_to_doc.item())
                    
                else:
                    generated_tokens = output


                text = tokenizer.batch_decode(generated_tokens,skip_special_tokens=True, clean_up_tokenization_spaces=True)[0].strip()
                decoded_preds.append(text)
                writer.write(text+'\n')

        # Some simple post-processing
        decoded_preds, decoded_labels = postprocess_text(decoded_preds, decoded_labels)
        result = metric.compute(predictions=decoded_preds, references=decoded_labels, use_stemmer=True)
        result = {key: value.mid.fmeasure * 100 for key, value in result.items()}
        print("prediction result:")
        print(result)
        with open(prediction_result_file, "w", encoding="utf-8") as f:
            data_str = json.dumps(result)
            f.write(data_str+'\n')

        if model_args.model_analysis:
            with open(model_analysis_file_path, "w", encoding="utf-8") as f:
                f.write("encoder self attn std dev:\n"+str(self_attn_analysis_list)+"\n")
                f.write("encoder self doc attn avg:\n"+str(self_doc_attn_analysis_list)+"\n")
                f.write("decoder cross attn std dev:\n"+str(cross_attn_analysis_list)+"\n")
            print("encoder self attn std dev:", self_attn_analysis_list)
            print("encoder self doc attn avg:", self_doc_attn_analysis_list)
            print("decoder cross attn std dev:", cross_attn_analysis_list)


    if training_args.push_to_hub:
        trainer.push_to_hub()

    return results


def _mp_fn(index):
    # For xla_spawn (TPUs)
    main()


if __name__ == "__main__":
    main()