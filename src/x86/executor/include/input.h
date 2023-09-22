/// File: Header for the input parser
///
// Copyright (C) Microsoft Corporation
// SPDX-License-Identifier: MIT

#ifndef _X86_EXECUTOR_INPUT_H_
#define _X86_EXECUTOR_INPUT_H_

#include "sandbox.h"

typedef uint64_t input_fragment_size_t;
typedef uint64_t input_fragment_permissions_field_t;
typedef uint64_t input_fragment_reserved_field_t;

typedef struct
{
    input_fragment_size_t size;
    input_fragment_permissions_field_t permission;
    input_fragment_reserved_field_t reserved;
} input_fragment_metadata_entry_t;

typedef struct
{
    char main_region[MAIN_REGION_SIZE];
    char faulty_region[FAULTY_REGION_SIZE];
    char reg_init_region[REG_INITIALIZATION_REGION_SIZE_ALIGNED];
} input_fragment_t;

typedef struct
{
    size_t metadata_size;
    size_t data_size;
    input_fragment_metadata_entry_t *metadata;
    input_fragment_t *data;
} input_batch_t;

#define MAX_INPUTS        (1024 * 1024)
#define BATCH_HEADER_SIZE 16 // sizeof(n_actors) + sizeof(n_inputs)
#define FRAGMENT_SIZE_ALIGNED                                                                      \
    (MAIN_REGION_SIZE + FAULTY_REGION_SIZE + REG_INITIALIZATION_REGION_SIZE_ALIGNED)

extern input_batch_t *inputs;
extern size_t n_inputs;

char *get_input_fragment(uint64_t input_id, uint64_t actor_id);
char *get_input_fragment_unsafe(uint64_t input_id, uint64_t actor_id);
ssize_t parse_input_buffer(const char *buf, size_t count, bool *finished);
bool input_parsing_completed(void);

int init_input_manager(void);
void free_input_parser(void);

#endif // _X86_EXECUTOR_INPUT_H_
