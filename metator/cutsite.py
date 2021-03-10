#!/usr/bin/env python3
# coding: utf-8

"""Cut the reads at their ligation sites.

The module will cut at ligation sites of the given enzyme. It will from the
original fastq (gzipped or not) files create new gzipped fastq files with
combinations of the fragaments of reads obtains by cutting at the ligation sites
of the reads.

There are three choices to how combine the fragments. 1. "for_vs_rev": All the
combinations are made between one forward fragment and one reverse fragment. 2.
"all": All 2-combinations are made. 3. "pile": Only combinations between
adjacent fragments in the initial reads are made.

This module contains the following functions:
    - cut_liagtion_sites
    - cutsite_read
    - write_pair
    - Writer    
"""


import gzip
import multiprocessing
import pyfastx
import re
import sys
import metator.io as mio
from metator.log import logger
from os.path import join, basename


def cut_ligation_sites(fq_for, fq_rev, enzyme, mode, outdir, n_cpu):
    """Create new reads to manage pairs with a digestion and create multiple
    pairs to take into account all the contact present.

    The function write two files for both the forward and reverse fastq with the
    new reads. The new reads have at the end of the ID ":1" added to
    differentiate the different pairs created from one read.

    To make the function faster, for each reads only the first site of ligation
    is kept and the algorithm stop to search for others sites as the probability
    is very low. We already have very few pairs with ligation sites in both
    reads.

    Parameters
    ----------
    fq_for : str
        Path to all the forward fastq file to digest separated by a comma.
    fq_rev : str
        Path to all the reverse fatsq file to digest separated by a comma..
    enzyme : str
        The list of restriction enzyme used to digest the genome separated by a
        comma. Example: DpnII,HinfI
    mode : str
        Mode to use to make the digestion. Three values possible: "all",
        "for_vs_rev", "pile".
    outdir : str
        Path for the ouput directory. The fastq will have their initial names
        with a suffix added "_digested".
    n_cpu : int
        Number of CPUs.
    """

    # Process the ligation sites given
    ligation_sites = mio.process_enzyme(enzyme)

    # Defined stop_token and stack_size for processing
    stop_token = "STOP"
    max_stack_size = 1000

    # Manage multiple fastq
    list_fq_for = fq_for.split(",")
    list_fq_rev = fq_rev.split(",")

    output_list_for = []
    output_list_rev = []

    # Sanity check
    if len(list_fq_for) != len(list_fq_rev):
        logger.error("Number of forward and reverse files are different")
        sys.exit(1)

    for i in range(len(list_fq_for)):
        fq_for = list_fq_for[i]
        fq_rev = list_fq_rev[i]

        logger.info("Reads in progress:\n{0}\n{1}".format(fq_for, fq_rev))

        # Create the two output file and add them to the list
        name_for = basename(fq_for).split(".")[0]
        name_rev = basename(fq_rev).split(".")[0]
        output_for = join(outdir, "{0}_digested.fq.gz".format(name_for))
        output_rev = join(outdir, "{0}_digested.fq.gz".format(name_rev))
        output_list_for.append(output_for)
        output_list_rev.append(output_rev)

        # Create count to have an idea of the digested pairs repartition.
        original_number_of_pairs = 0
        final_number_of_pairs = 0
        new_reads_for = ""
        new_reads_rev = ""
        current_stack = 0

        # Start parrallel threading to compute the
        ctx = multiprocessing.get_context("spawn")
        queue = multiprocessing.Queue(n_cpu - 1)
        writer_process = multiprocessing.Process(
            target=Writer, args=(output_for, output_rev, queue, stop_token)
        )
        writer_process.start()

        # Iterate on all pairs
        for read_for, read_rev in zip(
            pyfastx.Fastq(fq_for, build_index=False),
            pyfastx.Fastq(fq_rev, build_index=False),
        ):

            # Count the numbers of original reads processed.
            original_number_of_pairs += 1

            # Count for stack size.
            current_stack += 1

            # Extract components of the reads.
            for_name, for_seq, for_qual = read_for
            rev_name, rev_seq, rev_qual = read_rev

            # Sanity check to be sure all reads are with their mate.
            if for_name != rev_name:
                logger.error(
                    "The fastq files contains reads not sorted :\n{0}\n{1}".format(
                        read_for.id, read_rev.id
                    )
                )
                sys.exit(1)

            # Cut the forward and reverse reads at the ligation sites.
            for_seq_list, for_qual_list = cutsite_read(
                ligation_sites, for_seq, for_qual
            )
            rev_seq_list, rev_qual_list = cutsite_read(
                ligation_sites, rev_seq, rev_qual
            )

            # Write the new combinations of fragments.
            new_reads_for, new_reads_rev, final_number_of_pairs = write_pair(
                new_reads_for,
                new_reads_rev,
                for_name,
                for_seq_list,
                for_qual_list,
                rev_seq_list,
                rev_qual_list,
                mode,
                final_number_of_pairs,
            )

            # If stack full, add it in the queue.
            if current_stack == max_stack_size:

                # Add the pair in the queue.
                pairs = (new_reads_for.encode(), new_reads_rev.encode())
                queue.put(pairs)

                # Empty the stack
                current_stack = 0
                new_reads_for = ""
                new_reads_rev = ""

        # End the parallel processing.
        pairs = (new_reads_for.encode(), new_reads_rev.encode())
        queue.put(pairs)
        queue.put(stop_token)
        writer_process.join()

        # Return information on the different pairs created
        logger.info(
            "Number of pairs before digestion: {0}".format(
                original_number_of_pairs
            )
        )
        logger.info(
            "Number of pairs after digestion: {0}".format(final_number_of_pairs)
        )

    # Create list of the new localization of the FastQ.
    output_for = ",".join(output_list_for)
    output_rev = ",".join(output_list_rev)
    return output_for, output_rev


def cutsite_read(ligation_sites, seq, qual):
    """Find ligation sites in a given sequence.

    Parameters:
    -----------
    ligation_sites : str
        Regex of all possible ligations according to the given enzymes.
    seq : str
        Sequence where to search for ligation_sites.
    qual : str
        Quality values of the sequence given.

    Returns:
    --------
    list of str
        List of string of the sequences. The split is made at the start of the
        ligation sites.
    list of str
        List of string of the qualities.

    Examples:
    ---------
    >>> cutsite_read("GA.TA.TC", "AAGAGTATTC", "FFF--FAFAF")
    (['AA', 'GAGTATTC'], ['FF', 'F--FAFAF'])
    """

    # Find the ligation sites.
    ligation_sites_list = []
    if re.search(ligation_sites, seq):
        ligation_sites_list = [
            site.start() for site in re.finditer(ligation_sites, seq)
        ]
    ligation_sites_list.append(len(seq))

    # Split the sequences on the ligation sites.
    seq_list = []
    qual_list = []
    left_site = 0
    for right_site in ligation_sites_list:
        seq_list.append(seq[left_site:right_site])
        qual_list.append(qual[left_site:right_site])
        left_site = right_site

    return seq_list, qual_list


def write_pair(
    new_reads_for,
    new_reads_rev,
    name,
    for_seq_list,
    for_qual_list,
    rev_seq_list,
    rev_qual_list,
    mode,
    final_number_of_pairs,
):
    """Function to write one pair with the combinations of fragment depending on
    the chosen mode.

    Parameters:
    -----------
    new_reads_for : str
        Stack of the new forward reads ready to be written.
    new_reads_rev : str
        Stack of the new reverse reads ready to be written.
    name : str
        Name of the fastq read.
    for_seq : str
        Forward sequence of the fastq read.
    for_qual : str
        Forward quality of the fastq read.
    rev_seq : str
        Reverse sequence of the fastq read.
    rev_qual : str
        Reverse quality of the fastq read.
    mode : str
        Mode to use to make the digestion. Three values possible: "all",
        "for_vs_rev", "pile".
    final_numbers_of_pairs : int
        Count of pairs after cutting.

    Returns:
    --------
    str
        Stack of forward reads ready to be written with the last pairs added.
    str
        Stack of reverse reads ready to be written with the last pairs added.
    int
        Count of pairs after cutting.
    """

    # Mode "for_vs_rev": Make contacts only between fragments from different
    # reads (one fragment from forward and one from reverse).
    if mode == "for_vs_rev":
        for i in range(len(for_seq_list)):
            for j in range(len(rev_seq_list)):
                final_number_of_pairs += 1
                new_reads_for += "@%s\n%s\n+\n%s\n" % (
                    name + ":" + str(i) + str(j),
                    for_seq_list[i],
                    for_qual_list[i],
                )
                new_reads_rev += "@%s\n%s\n+\n%s\n" % (
                    name + ":" + str(i) + str(j),
                    rev_seq_list[j],
                    rev_qual_list[j],
                )

    #  Mode "all": Make all the possible contacts between the fragments.
    elif mode == "all":
        seq_list = for_seq_list + rev_seq_list
        qual_list = for_qual_list + rev_qual_list
        for i in range(len(seq_list)):
            for j in range(i + 1, len(seq_list)):
                final_number_of_pairs += 1
                new_reads_for += "@%s\n%s\n+\n%s\n" % (
                    name + ":" + str(i) + str(j),
                    seq_list[i],
                    qual_list[i],
                )
                new_reads_rev += "@%s\n%s\n+\n%s\n" % (
                    name + ":" + str(i) + str(j),
                    seq_list[j],
                    qual_list[j],
                )

    # Mode "pile": Only make contacts bewteen two adjacent fragments.
    elif mode == "pile":
        seq_list = for_seq_list + rev_seq_list
        qual_list = for_qual_list + rev_qual_list
        for i in range(len(seq_list) - 1):
            final_number_of_pairs += 1
            new_reads_for += "@%s\n%s\n+\n%s\n" % (
                name + ":" + str(i) + str(i + 1),
                seq_list[i],
                qual_list[i],
            )
            new_reads_rev += "@%s\n%s\n+\n%s\n" % (
                name + ":" + str(i) + str(i + 1),
                seq_list[i + 1],
                qual_list[i + 1],
            )

    return new_reads_for, new_reads_rev, final_number_of_pairs


def Writer(output_for, output_rev, queue, stop_token):
    """Function to write the pair throw parallel processing.

    Parameters:
    -----------
    output_for : str
        Path to the output forward compressed fastq file.
    output_rev : str
        Path to the output reverse compressed fastq file.
    queue : multiprocessing.queues.Queue
        Queue for the multiprocesing of the whole file.
    stop_token : str
        Token to signal that the end of the file have been reached.
    """
    with gzip.open(output_for, "wb") as for_fq, gzip.open(
        output_rev, "wb"
    ) as rev_fq:
        while True:
            line = queue.get()
            if line == stop_token:
                return 0
            for_fq.write(line[0])
            rev_fq.write(line[1])
