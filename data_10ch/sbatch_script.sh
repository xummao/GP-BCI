uid=$1
for EMG in 0 2 4; do
    for DT in 0 10 20 40 60 80 100; do
	sbatch chruns2d_script.sh $EMG $DT $uid
	sbatch chruns2d_script.sh $EMG $DT $uid sa
    done
done
