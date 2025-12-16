%Written by Ben Shin, 11/6/2025. Email benwshin@gmail.com for help.
%Raw reads must be combined into a single csv file. First row = wavelength
%Next rows should be in order from first to last plate. A1 should be labeled [GuHCl] (M).
%Check realcombined.csv for an example
%change realcombined.csv to name of the file as needed 
inputFile = 'realcombined.csv';
rawData = readcell(inputFile);

rawData = cellfun(@(x) string(x), rawData, 'UniformOutput', false);
rawData(cellfun(@(x) isequal(x,"<missing>"), rawData)) = {''};

header = rawData(1, :);

data = rawData(2:end, :);

%Concentration of GuHCl. Feel free to change if practical changes.
firstColValues = [0, 0.4, 0.8, 1.2, 1.6, 2, 2.4, 2.8, 3.2, 3.6, 4, 4.4, 4.8, 5.2, 5.6, 6];

%groups.csv has to have the exact number of group names as there are data.
%ie column number in realcombined.csv/16 should be an integer. Make sure
%students use the right wells!
groupFile = 'groups.csv'
groupNamesRaw = readcell(groupFile, 'Delimiter', ',');
groupNames = string(groupNamesRaw(:,1));

if numGroups > length(groupNames)
    error('Not enough group names for number of reads')
end

%If in the future, there are less datapoints, replace the 16 in all of the script
%with the number of GuHCl concentrations used. Otherwise the code won't
%run.
numRows = size(data, 1);
numGroups = ceil(numRows/16);

for i = 1:numGroups
    startIdx = (i-1)*16+1;
    endIdx = min(i*16, numRows);

    subset = data(startIdx:endIdx, :);
    
    numSubsetRows = size(subset,1);
    if numSubsetRows > length(firstColValues)
        error('More subset rows than replacement values');
    end
    subset(:,1) = num2cell(firstColValues(1:numSubsetRows));

    outCell = [header; subset];

    groupName = groupNames(i);
    safeName = replace(groupName, ' ', '_');
    safeName = regexprep(safeName, '[^\w]', '');

    outputFile = sprintf('%s.csv', safeName);

    writecell(outCell, outputFile);

    fprintf('%s.csv', outputFile, startIdx+1,endIdx+1);
end

fprintf('Split into %d files.\n', numGroups);
