"""
File: Parser of ELF files for x86 architecture

Copyright (C) Microsoft Corporation
SPDX-License-Identifier: MIT
"""
from typing import Dict, List, Tuple, NoReturn

from subprocess import run
from elftools.elf.elffile import ELFFile, SymbolTableSection  # type: ignore
from ..interfaces import ElfSection, Symbol, TestCase, Instruction
from ..util import Logger
from .x86_target_desc import X86TargetDesc

# ==================================================================================================
#  Custom Data Types
# ==================================================================================================
SectionID = int
InstructionAddress = int
SectionMap = Dict[InstructionAddress, Instruction]
TestCaseMap = Dict[SectionID, SectionMap]


class SectionMetadata:
    name: str = ""
    elf_section_id: int = -1
    id_: SectionID = -1
    offset: int = -1
    size: int = -1


class FunctionMetadata:
    name: str = ""
    elf_section_id: int = -1
    id_: int = -1
    parent_id: int = -1
    offset: int = -1


# ==================================================================================================
# Parser
# ==================================================================================================


def elf_parser_error(msg: str) -> NoReturn:
    logger = Logger()
    logger.error("[X86ElfParser] Error while parsing assembly\n"
                 f"       Issue: {msg}", print_last_tb=True)


class X86ElfParser:

    def __init__(self, target_desc: X86TargetDesc) -> None:
        self.target_desc = target_desc
        self.LOG = Logger()

    def parse(self, test_case: TestCase, obj_file: str) -> None:
        """
        Parse the ELF file and add the following data to the test case:
        - ELF section data
        - ELF symbol table data
        - instruction addresses
        """

        # get metadata from the ELF file
        section_entries, function_entries, exit_addr = self._parse_elf_symbol_table(obj_file)
        instruction_addresses = self._parse_objdump_output(obj_file)

        # add collected data to the test case
        address_map: Dict[int, Dict[int, Instruction]] = {}
        for section in section_entries:
            # store actor data
            actor_name = section.name
            actor = test_case.get_actor_by_name(actor_name)
            actor.elf_section = ElfSection(section.id_, section.offset, section.size)

            # find functions belonging to this actor
            functions = [f for f in function_entries if f.parent_id == section.id_]

            # process functions
            counter = 0
            address_map[actor.id_] = {}
            for func in functions:
                # store function data
                assert func.offset == instruction_addresses[section.name][counter], \
                    f"offsets: {func.offset} {instruction_addresses[section.name][counter]}"
                test_case.symbol_table.append(
                    Symbol(
                        aid=func.parent_id,
                        type_=0,
                        offset=func.offset,
                        arg=func.id_,
                    ))

                # process instructions
                function_object = test_case.get_function_by_name(func.name)
                assert function_object.owner == actor
                for bb in list(function_object) + [function_object.exit]:
                    for inst in list(bb) + bb.terminators:
                        address = instruction_addresses[section.name][counter]

                        # store instruction data
                        inst.section_id = section.id_
                        inst.section_offset = address
                        address_map[actor.id_][address] = inst

                        # add macros to the symbol table
                        if inst.name == "MACRO":
                            test_case.symbol_table.append(
                                self._symbol_from_macro_inst(inst, section_entries,
                                                             function_entries))

                        counter += 1

        # the last instruction in .data.0_host is the test case exit, and it must map to a NOP
        address_map[0][exit_addr] = Instruction("NOP", False, "BASE-NOP", True)

        test_case.address_map = address_map

    def _parse_elf_symbol_table(self, obj_file) \
            -> Tuple[List[SectionMetadata], List[FunctionMetadata], int]:
        """
        Parse the ELF symbol table to get the following information:
        - section names and ids, ordered by section id
        - function names and ids, ordered by parent section id and offset
        - exit address of the test case
        """

        section_entries: List[SectionMetadata] = []
        function_entries: List[FunctionMetadata] = []
        exit_addr: int = -1

        # get the ELF symbol table data
        with open(obj_file, "rb") as f:
            data = ELFFile(f)

            # sanity check: we build objects in such a way that there should be no segments
            assert data.num_segments() == 0, f"{data.num_segments()}"

            # collect section info
            for id_, s in enumerate(data.iter_sections()):
                if ".data." not in s.name:
                    continue
                s_entry = SectionMetadata()
                s_entry.elf_section_id = id_
                s_entry.name = s.name.split(".")[2]
                s_entry.offset = s['sh_offset']
                s_entry.size = s['sh_size']
                section_entries.append(s_entry)

            # get addresses of functions and macros
            symtab: SymbolTableSection = data.get_section_by_name(".symtab")  # type: ignore
            for s in symtab.iter_symbols():
                name = s.name
                if name.startswith(".function"):
                    f_entry = FunctionMetadata()
                    f_entry.name = name
                    f_entry.elf_section_id = s['st_shndx']
                    f_entry.offset = s.entry.st_value
                    function_entries.append(f_entry)

                if ".test_case_exit" in name:
                    exit_addr = s.entry.st_value
        assert exit_addr != -1, "Failed to find exit address"

        # make sure that sections and functions are properly ordered
        section_entries.sort(key=lambda x: x.elf_section_id)
        function_entries.sort(key=lambda x: (x.elf_section_id, x.offset))

        # assign consecutive ids to functions and sections
        elf_id_to_section_id = {}
        for i, s_entry in enumerate(section_entries):
            s_entry.id_ = i
            elf_id_to_section_id[s_entry.elf_section_id] = i
        for i, f_entry in enumerate(function_entries):
            f_entry.id_ = i
            f_entry.parent_id = elf_id_to_section_id[f_entry.elf_section_id]

        return section_entries, function_entries, exit_addr

    def _parse_objdump_output(self, obj_file) -> Dict[str, List[int]]:
        """
        Parse the output of objdump to get the addresses of all instructions

        returns: a dictionary mapping section names to lists of its instruction addresses
        """
        instruction_addresses: Dict[str, List[int]] = {}
        dump = run(
            f"objdump --no-show-raw-insn -D -M intel -m i386:x86-64 {obj_file} "
            "| awk '/ [0-9a-f]+:/{print $1} /section/{print $0}'",
            shell=True,
            check=True,
            capture_output=True)

        section_name = ""
        for line in dump.stdout.decode().split("\n"):
            if not line:
                continue

            if "section" in line:
                if ".data." not in line:
                    section_name = ""
                    continue

                try:
                    section_name = line.split()[-1][:-1]
                    section_name = section_name.split(".")[2]
                except ValueError:
                    section_name = ""
                if section_name == "":
                    self.LOG.error(f"Invalid actor label or undefined actor: {line.split()[-1]}")
                instruction_addresses[section_name] = []
                continue
            assert section_name != "", "Failed to parse objdump output (section_name)"

            instruction_addresses[section_name].append(int(line[:-1], 16))
        return instruction_addresses

    def _symbol_from_macro_inst(self, inst: Instruction, symbol_entries: List[SectionMetadata],
                                function_entries: List[FunctionMetadata]) -> Symbol:
        """
        Convert a macro instruction to a symbol table intry by parsing its symbolic arguments
        according to the macro specification (see x86_target_desc.py).

        Example:
        - Input (macro instruction): MACRO 1, .0_host.function_1
        - Processing:
            type: 1 (actor switch)
            arg 1: 0_host -> 0 (offset of section 0_host)
            arg 2: function_1 -> 12 (offset of function function_1 within section 0_host)
            arg 3: none
            arg 4: none
            compressed macro argument: 0 + (12 << 16) + (0 << 32) + (0 << 48) = 786432
        - Output (symbol table entry): Symbol(0, 1, 0, 786432)
        """

        def get_section_id(name: str) -> int:
            name = name.lower()
            for entry in symbol_entries:
                if entry.name == name:
                    return entry.id_
            elf_parser_error(f"Macro references an unknown actor {name}")

        def get_function_id(name: str) -> int:
            name = name.lower()
            for entry in function_entries:
                if entry.name == name:
                    return entry.id_
            elf_parser_error(f"Macro references an unknown function {name}")

        assert inst.name == "MACRO"
        macro_name = inst.operands[0].value[1:]
        if macro_name.lower() not in self.target_desc.macro_specs:
            elf_parser_error(f"Unknown macro {macro_name} in {inst}")
        macro_spec = self.target_desc.macro_specs[macro_name.lower()]

        # convert macro operands to compressed symbol arguments
        str_args = inst.operands[1].value.split('.')[1:]
        symbol_args: int = 0
        for i, arg in enumerate(str_args):
            if macro_spec.args[i] == "":
                continue
            elif macro_spec.args[i] == "actor_id":
                symbol_args += (get_section_id(arg) << i * 16)
            elif macro_spec.args[i] == "function_id":
                symbol_args += (get_function_id("." + arg) << i * 16)
            else:
                raise ValueError(f"Invalid macro argument {macro_spec.args[i]}")

        symbol = Symbol(
            aid=inst.section_id,
            type_=macro_spec.type_,
            offset=inst.section_offset,
            arg=symbol_args,
        )
        return symbol