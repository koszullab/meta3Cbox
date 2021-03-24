#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Core tools to build I/O for metaTOR

This mdoule contains all core I/O functions:
    - check_checkm
    - check_fasta_index
    - check_louvain_function
    - generate_temp_dir
    - process_ligation_sites
    - read_compressed
    - read_results_checkm
    - sort_pairs
    - write_checkm_summary
"""

import bz2
import gzip
import io
import os
import pandas as pd
import pathlib
import re
import subprocess as sp
import sys
import zipfile
from Bio import SeqIO
from Bio.Restriction import RestrictionBatch
from Bio.Seq import Seq
from metator.log import logger
from os.path import join, exists
from random import getrandbits


def check_checkm():
    """
    Function to test if CheckM is in the path.

    Returns:
    --------
    bool:
        True if checkM found in the path, False otherwise.
    """
    try:
        checkm = sp.check_output("checkm", stderr=sp.STDOUT, shell=True)
    except sp.CalledProcessError:
        logger.warning(
            "Cannot find 'checkm' in your path please install it or add it in your path."
        )
        return False
    return True


def check_fasta_index(ref, mode="bowtie2"):
    """
    Function from hicstuff.io (https://github.com/koszullab/hicstuff/)
    Checks for the existence of a bowtie2 or bwa index based on the reference
    file name.

    Parameters:
    -----------
    ref : str
        Path to the reference genome.
    mode : str
        The alignment software used to build the index. bowtie2 or bwa. If any
        other value is given, the function returns the reference path.

    Returns:
    --------
    str
        The bowtie2 or bwa index basename. None if no index was found
    """
    ref = pathlib.Path(ref)
    if mode == "bowtie2":
        # Bowtie2 should have 6 index files
        bt2_idx_files = list(ref.parent.glob("{}*bt2*".format(ref.name)))
        index = None if len(bt2_idx_files) < 6 else bt2_idx_files
    elif mode == "bwa":
        refdir = str(ref.parent)
        refdir_files = os.listdir(refdir)
        bwa_idx_files = [
            join(refdir, f)
            for f in refdir_files
            if re.search(r".*\.(sa|pac|bwt|ann|amb)$", f)
        ]
        index = None if len(bwa_idx_files) < 5 else bwa_idx_files
    else:
        index = [ref]
    if index is not None:
        # Convert the PosixPath objects to strings and get the longest common
        # prefix to obtain index basename (without the dot)
        index = os.path.commonprefix(list(map(str, index))).strip(".")
    return index


def check_is_fasta(in_file):
    """
    Function from hicstuff.io (https://github.com/koszullab/hicstuff/)
    Checks whether input file is in fasta format.

    Parameters
    ----------
    in_file : str
        Path to the input file.

    Returns
    -------
    bool :
        True if the input is in fasta format, False otherwise
    """
    try:
        with open(in_file, "r") as handle:
            test = any(SeqIO.parse(handle, "fasta"))
    except FileNotFoundError:
        test = False

    return test


def check_louvain_cpp(louvain_path):
    """Function to check is the Louvain functions are callable.

    Parameters:
    -----------
    louvain_path : str
        Path of the directory where the Louvain functions are.

    Returns:
    --------
    bool:
        Boolean value describing either the Louvain functions are callable or
        not.
    """

    # Look for the path to call the functions:
    louvain = join(louvain_path, "louvain")
    convert_net = join(louvain_path, "convert_net")
    hierarchy = join(louvain_path, "hierarchy")

    # Check convert:
    try:
        convert_net = sp.check_output(
            "{0} --help".format(convert_net), stderr=sp.STDOUT, shell=True
        )
    except sp.CalledProcessError:
        logger.warning(
            "Cannot find the 'convert_net' function from Louvain path."
        )
        return False

    # Check louvain:
    try:
        louvain = sp.check_output(
            "{0} --help".format(louvain), stderr=sp.STDOUT, shell=True
        )
    except sp.CalledProcessError:
        logger.warning("Cannot find the 'louvain' function from Louvain path.")
        return False

    # Check hierarchy:
    try:
        hierarchy = sp.check_output(
            "{0} --help".format(hierarchy), stderr=sp.STDOUT, shell=True
        )
    except sp.CalledProcessError:
        logger.warning(
            "Cannot find the convert_net function from Louvain path."
        )
        return False

    return True


def generate_temp_dir(path):
    """Temporary directory generation

    Function from hicstuff.io (https://github.com/koszullab/hicstuff/)
    Generates a temporary file with a random name at the input path.

    Parameters:
    -----------
    path : str
        The path at which the temporary directory will be created.

    Returns:
    --------
    str
        The path of the newly created temporary directory.
    """
    exist = True
    while exist:
        # Keep trying random directory names if they already exist
        directory = str(hex(getrandbits(32)))[2:]
        full_path = join(path, directory)
        if not exists(full_path):
            exist = False
    try:
        os.makedirs(full_path)
    except PermissionError:
        raise PermissionError(
            "The temporary directory cannot be created in {}. "
            "Make sure you have write permission.".format(path)
        )
    return full_path


def process_enzyme(enzyme):
    """Function to return a regex which corresponds to all possible ligation
    sites given a set of enzyme.

    Parameters:
    -----------
    enzyme : str
        String that contains the names of the enzyme separated by a comma.

    Returns:
    --------
    str :
        Regex that corresponds to all possible ligation sites given a set of
        enzyme.

    Examples:
    ---------
    >>> process_enzyme('DpnII')
    'GATCGATC'
    >>> process_enzyme('DpnII,HinfI')
    'GA.TA.TC|GA.TGATC|GATCA.TC|GATCGATC'
    """

    # Split the str on the comma to separate the different enzymes.
    enzyme = enzyme.split(",")

    # Check on Biopython dictionnary the enzyme.
    rb = RestrictionBatch(enzyme)

    # Initiation:
    give_list = []
    accept_list = []
    ligation_list = []

    # Iterates on the enzymes.
    for enz in rb:

        # Extract restriction sites and look for cut sites.
        site = enz.elucidate()
        fw_cut = site.find("^")
        rev_cut = site.find("_")

        # Process "give" site. Remove N on the left (useless).
        give_site = site[:rev_cut].replace("^", "")
        while give_site[0] == "N":
            give_site = give_site[1:]
        give_list.append(give_site)

        # Process "accept" site. Remove N on the rigth (useless).
        accept_site = site[fw_cut + 1 :].replace("_", "")
        while accept_site[-1] == "N":
            accept_site = accept_site[:-1]
        accept_list.append(accept_site)

    # Iterates on the two list to build all the possible HiC ligation sites.
    for give_site in give_list:
        for accept_site in accept_list:
            # Replace "N" by "." for regex searching of the sites
            ligation_list.append((give_site + accept_site).replace("N", "."))
            ligation_list.append(
                str(Seq(give_site + accept_site).reverse_complement()).replace(
                    "N", "."
                )
            )

    # Build the regex for any ligation sites.
    pattern = "|".join(sorted(list(set(ligation_list))))
    return pattern


def read_compressed(filename):
    """Read compressed file

    Function from hicstuff.io (https://github.com/koszullab/hicstuff/)
    Opens the file in read mode with appropriate decompression algorithm.

    Parameters:
    -----------
    filename : str
        The path to the input file

    Returns:
    --------
    file-like object
        The handle to access the input file's content
    """

    # Standard header bytes for diff compression formats
    comp_bytes = {
        b"\x1f\x8b\x08": "gz",
        b"\x42\x5a\x68": "bz2",
        b"\x50\x4b\x03\x04": "zip",
    }

    max_len = max(len(x) for x in comp_bytes)

    def file_type(filename):
        """Guess file type

        Compare header bytes with those in the file and return type.
        """
        with open(filename, "rb") as f:
            file_start = f.read(max_len)
        for magic, filetype in comp_bytes.items():
            if file_start.startswith(magic):
                return filetype
        return "uncompressed"

    # Open file with appropriate function
    comp = file_type(filename)
    if comp == "gz":
        return gzip.open(filename, "rt")
    elif comp == "bz2":
        return bz2.open(filename, "rt")
    elif comp == "zip":
        zip_arch = zipfile.ZipFile(filename, "r")
        if len(zip_arch.namelist()) > 1:
            raise IOError(
                "Only a single fastq file must be in the zip archive."
            )
        else:
            # ZipFile opens as bytes by default, using io to read as text
            zip_content = zip_arch.open(zip_arch.namelist()[0], "r")
            return io.TextIOWrapper(zip_content, encoding="utf-8")
    else:
        return open(filename, "r")


def read_results_checkm(checkm_file, checkm_taxonomy_file):
    """Function to transform the output summary file of checkm into a
    dictionnary.

    Parameters:
    -----------
    checkm_file : str
        Path to the summary output file of CheckM.

    Returns:
    --------
    dict:
        Dictionnary of the output of checkm with binID as keys and with three
        values lineage, completness and contamination.
    """

    # Create an empty dictionnary
    checkm_summary = dict()

    # Read the checkm summary file.
    with open(checkm_file, "r") as checkm_lines:
        for line in checkm_lines:
            # Only keep informative lines which start with a space.
            if line[0] == " ":
                line = line.split()
                checkm_summary[line[0]] = {
                    "lineage": line[1],
                    "completness": line[6],
                    "contamination": line[7],
                    "size": line[9],
                    "contigs": line[12],
                    "N50": line[14],
                    "longest_contig": line[18],
                    "GC": line[19],
                    "coding_density": line[21],
                    "taxonomy": "-"
                }
    
    # Read the taxonomy file.
    with open(checkm_taxonomy_file, "r") as checkm_lines:
        for line in checkm_lines:
            # Only keep informative lines which start with a space.
            if line[0] == " ":
                line = line.split()
                checkm_summary[line[0]]["taxonomy"] = line[3]

    # remove the bin header
    checkm_summary.pop("Bin")

    return checkm_summary


def retrieve_fasta(in_file, tmpdir):
    """
    Function to retrieve fasta from the given reference file. If index is given
    retrieve it using bowtie2 inspect. Thraw an error if not a fasta or bowtie2
    index.

    Parameters:
    -----------
    in_file : str
        Path to the reference file given.
    tmpdir : str
        Path to the temp directory to write the fasta if necessary.

    Returns:
    --------
    str:
        Path to the fasta file.
    """
    if check_is_fasta(in_file):
        fasta = in_file
    else:
        if check_fasta_index(in_file):
            logger.info("Retrieve fasta from bowtie2 index.")
            fasta = join(tmpdir, "assembly.fa")
            cmd = "bowtie2-inspect {0} > {1}".format(in_file, fasta)
            process = sp.Popen(cmd, shell=True, stdout=sp.PIPE)
            out, err = process.communicate()
        else:
            logger.error(
                "Please give as a reference a bowtie2 index or a fasta."
            )
            sys.exit(1)
    return fasta


def sort_pairs(in_file, out_file, tmp_dir=None, threads=1, buffer="2G"):
    """
    Adapted function from hicstuff.io (https://github.com/koszullab/hicstuff/)
    Sort a pairs file in batches using UNIX sort.

    Parameters:
    -----------
    in_file : str
        Path to the unsorted input file
    out_file : str
        Path to the sorted output file.
    keys : list of str
        list of columns to use as sort keys. Each column can be one of readID,
        chr1, pos1, chr2, pos2, frag1, frag2. Key priorities are according to
        the order in the list.
    tmp_dir : str
        Path to the directory where temporary files will be created. Defaults
        to current directory.
    threads : int
        Number of parallel sorting threads.
    buffer : str
        Buffer size used for sorting. Consists of a number and a unit.
    """
    # TODO: Write a pure python implementation to drop GNU coreutils depencency,
    # could be inspired from: https://stackoverflow.com/q/14465154/8440675

    # Check if UNIX sort version supports parallelism
    parallel_ok = True
    sort_ver = sp.Popen(["sort", "--version"], stdout=sp.PIPE)
    sort_ver = (
        sort_ver.communicate()[0]
        .decode()
        .split("\n")[0]
        .split(" ")[-1]
        .split(".")
    )
    # If so, specify threads, otherwise don't mention it in the command line
    try:
        sort_ver = list(map(int, sort_ver))
        if sort_ver[0] < 8 or (sort_ver[0] == 8 and sort_ver[1] < 23):
            logger.warning(
                "GNU sort version is {0} but >8.23 is required for parallel "
                "sort. Sorting on a single thread.".format(
                    ".".join(map(str, sort_ver))
                )
            )
            parallel_ok = False
    # BSD sort has a different format and will throw error upon parsing. It does
    # not support parallel processes anyway.
    except ValueError:
        logger.warning(
            "Using BSD sort instead of GNU sort, sorting on a single thread."
        )
        parallel_ok = False

    # Sort pairs and append to file.
    with open(out_file, "a") as output:
        grep_proc = sp.Popen(["grep", "-v", "^#", in_file], stdout=sp.PIPE)
        sort_cmd = ["sort", "-S %s" % buffer] + ["-k1,1V", "-k2,2V"]
        if tmp_dir is not None:
            sort_cmd.append("--temporary-directory={0}".format(tmp_dir))
        if parallel_ok:
            sort_cmd.append("--parallel={0}".format(threads))
        sort_proc = sp.Popen(sort_cmd, stdin=grep_proc.stdout, stdout=output)
        sort_proc.communicate()


def write_checkm_summary(bin_summary, bin_summary_file):
    """Function to write the bin summary from dictionnary to table text file.

    Parameters:
    -----------
    bin_summary : dict
        Dictionnary with the output of the checkM of the bins.
    bin_summary_file : str
        Path to the output file to write the summary informations of the bins.
    """

    # Transform dictionnary to pandas DataFrame.
    bin_summary = pd.DataFrame.from_dict(bin_summary, orient="index")

    # Write the file.
    bin_summary.to_csv(bin_summary_file, sep="\t")
